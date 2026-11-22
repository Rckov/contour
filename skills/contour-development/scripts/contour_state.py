#!/usr/bin/env python3
"""Transactional local Contour state; not a same-user security boundary.

This module never invokes a shell, model, project command, or external mutation.
Adapters call begin-attempt, perform an already authorized operation, then submit
its observed receipt. An interrupted attempt requires observation before reuse.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid

from contour_ops import Invalid, control_location, new_control_location, storage_path

SCHEMA_VERSION = 1
STATES = frozenset({"clarifying", "ready", "running", "proposed", "accepted",
                    "blocked", "paused", "closed"})
ENDPOINTS = frozenset({"analysis", "demo", "patch", "integrated", "release-ready",
                       "deployed", "observed"})
TERMINAL = frozenset({"accepted", "closed"})
PENDING = frozenset({"pending", "unknown"})
MAX_JSON = 4 * 1024 * 1024
MAX_ITEMS = 1000
WORKSPACE_RESOURCE_PREFIX = "workspace:"


class StateError(ValueError):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details or {}

    def as_dict(self):
        return {"status": self.code, "message": self.message, "details": self.details}


def require(condition, message, code="INVALID", **details):
    if not condition:
        raise StateError(code, message, details)


def text(value, name, maximum=4096):
    require(isinstance(value, str) and bool(value.strip()) and
            len(value) <= maximum and "\x00" not in value,
            f"{name}: expected a nonempty bounded string")
    return value


def identifier(value, name):
    text(value, name, 200)
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/@-]*", value) is not None,
            f"{name}: invalid identifier")
    return value


def integer(value, name, minimum=0):
    require(type(value) is int and value >= minimum, f"{name}: invalid integer")
    return value


def number(value, name, minimum=0, positive=False):
    require(type(value) in (int, float), f"{name}: invalid finite number")
    try:
        converted = float(value)
    except OverflowError as exc:
        raise StateError("INVALID", f"{name}: number exceeds the finite runtime range") from exc
    require(math.isfinite(converted) and converted >= minimum and (not positive or converted > 0),
            f"{name}: invalid finite number")
    return converted


def sha(value, name):
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value),
            f"{name}: expected lowercase SHA-256")
    return value


def obj(value, name):
    require(isinstance(value, dict), f"{name}: expected object")
    return value


def array(value, name, nonempty=False):
    require(isinstance(value, list) and len(value) <= MAX_ITEMS and
            (bool(value) or not nonempty), f"{name}: invalid array")
    return value


def strings(value, name, nonempty=False):
    result = [text(v, name) for v in array(value, name, nonempty)]
    require(len(result) == len(set(result)), f"{name}: duplicates")
    return result


def encode(value):
    try:
        result = json.dumps(value, sort_keys=True, separators=(",", ":"),
                            ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise StateError("INVALID", "Request must contain finite JSON data") from exc
    require(len(result.encode()) <= MAX_JSON, "JSON record exceeds 4 MiB")
    return result


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


SCHEMA = """
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE tasks (
 id TEXT PRIMARY KEY, revision INTEGER NOT NULL, seq INTEGER NOT NULL,
 state TEXT NOT NULL, stale INTEGER NOT NULL DEFAULT 0, body TEXT NOT NULL,
 attempts_used INTEGER NOT NULL DEFAULT 0, seconds_used REAL NOT NULL DEFAULT 0,
 seconds_reserved REAL NOT NULL DEFAULT 0);
CREATE TABLE revisions (
 task_id TEXT NOT NULL REFERENCES tasks(id), revision INTEGER NOT NULL,
 body TEXT NOT NULL, last_state TEXT NOT NULL, acceptance TEXT,
 PRIMARY KEY(task_id, revision));
CREATE TABLE dependencies (
 task_id TEXT NOT NULL REFERENCES tasks(id), dep_id TEXT NOT NULL REFERENCES tasks(id),
 dep_revision INTEGER NOT NULL, PRIMARY KEY(task_id, dep_id));
CREATE TABLE claims (
 generation INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL REFERENCES tasks(id),
 revision INTEGER NOT NULL, worker_id TEXT NOT NULL,
 access TEXT NOT NULL DEFAULT 'write' CHECK(access IN ('read','write')), resources TEXT NOT NULL,
 expires_at REAL NOT NULL, released INTEGER NOT NULL DEFAULT 0, stopped_ref TEXT);
CREATE TABLE attempts (
 attempt_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
 revision INTEGER NOT NULL, generation INTEGER NOT NULL REFERENCES claims(generation),
 worker_id TEXT NOT NULL, operation_id TEXT NOT NULL, category TEXT NOT NULL,
 status TEXT NOT NULL, reserve_seconds REAL NOT NULL, actual_seconds REAL,
 binding TEXT NOT NULL, receipt TEXT, resolution_ref TEXT, effect_id TEXT);
CREATE TABLE effects (
 effect_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
 revision INTEGER NOT NULL, state TEXT NOT NULL, body TEXT NOT NULL,
 observation TEXT, attempt_id TEXT);
CREATE TABLE events (
 event_id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, revision INTEGER,
 event_type TEXT NOT NULL, payload TEXT NOT NULL, recorded_at REAL NOT NULL);
CREATE TABLE requests (
 request_id TEXT PRIMARY KEY, request_digest TEXT NOT NULL, result TEXT NOT NULL);
CREATE INDEX attempts_task ON attempts(task_id, revision);
CREATE INDEX claims_task ON claims(task_id, released);
CREATE INDEX dependencies_parent ON dependencies(dep_id);
"""


class Controller:
    def __init__(self, db: Path):
        self.db = self._storage_path(db)

    @staticmethod
    def _storage_path(value):
        try:
            return storage_path(value)
        except Invalid as error:
            raise StateError("INVALID", str(error)) from error

    def _connect(self, create=False):
        self._storage_path(self.db)
        self._storage_path(self.db.with_name(self.db.name + "-journal"))
        if not create:
            require(self.db.is_file() and not self.db.is_symlink(),
                    "State database is not initialized", "NOT_INITIALIZED")
        con = sqlite3.connect(self.db, timeout=2, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=2000")
        con.execute("PRAGMA synchronous=FULL")
        mode = con.execute("PRAGMA journal_mode").fetchone()[0]
        if mode != "delete":
            con.close()
            raise StateError("PROFILE", "State database requires DELETE journal mode")
        if not create:
            version = con.execute("PRAGMA user_version").fetchone()[0]
            if version != SCHEMA_VERSION:
                con.close()
                raise StateError("SCHEMA", "Unsupported state schema; retain or explicitly migrate it",
                                 {"found": version, "supported": SCHEMA_VERSION})
        return con

    @contextmanager
    def _transaction(self):
        con = None
        try:
            con = self._connect()
            con.execute("BEGIN IMMEDIATE")
            yield con
            con.execute("COMMIT")
        except sqlite3.Error as exc:
            if con is not None and con.in_transaction:
                con.execute("ROLLBACK")
            code = "BUSY" if "locked" in str(exc).lower() or "busy" in str(exc).lower() else "STORAGE"
            raise StateError(code, "State transaction failed", {"sqlite": str(exc)}) from exc
        except BaseException:
            if con is not None and con.in_transaction:
                con.execute("ROLLBACK")
            raise
        finally:
            if con is not None:
                con.close()

    def initialize(self, project_id, root, profile_digest):
        identifier(project_id, "project_id")
        project_root = Path(root).resolve()
        require(project_root.is_dir(), "Project root must exist")
        try:
            control_location(root, self.db)
        except Invalid as error:
            raise StateError("INVALID", str(error)) from error
        sha(profile_digest, "profile_digest")
        require(self.db.parent.is_dir(), "State database parent must exist")
        con = None
        try:
            con = self._connect(create=True)
            con.execute("BEGIN IMMEDIATE")
            version = con.execute("PRAGMA user_version").fetchone()[0]
            if version:
                require(version == SCHEMA_VERSION, "Unsupported existing state schema", "SCHEMA")
                stored = self._project(con)
                require((stored["project_id"], stored["root"], stored["profile_digest"]) ==
                        (project_id, str(project_root), profile_digest),
                        "Initialization conflicts with existing project identity", "CONFLICT")
                con.execute("COMMIT")
                return {"status": "OK", "project": stored, "replayed": True}
            require(not con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(),
                    "Refusing to initialize a nonempty unrelated database", "CONFLICT")
            for statement in SCHEMA.split(";"):
                if statement.strip():
                    con.execute(statement)
            project = {"schema_version": SCHEMA_VERSION, "project_id": project_id,
                       "root": str(project_root), "profile_digest": profile_digest,
                       "created_at": time.time()}
            con.execute("INSERT INTO metadata VALUES ('project', ?)", (encode(project),))
            con.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            self._event(con, None, "initialize", project)
            con.execute("COMMIT")
            return {"status": "OK", "project": project}
        except sqlite3.Error as exc:
            if con is not None and con.in_transaction:
                con.execute("ROLLBACK")
            raise StateError("STORAGE", "Initialization failed", {"sqlite": str(exc)}) from exc
        except BaseException:
            if con is not None and con.in_transaction:
                con.execute("ROLLBACK")
            raise
        finally:
            if con is not None:
                con.close()

    @staticmethod
    def _project(con):
        row = con.execute("SELECT value FROM metadata WHERE key='project'").fetchone()
        require(row is not None, "Missing project metadata", "SCHEMA")
        return json.loads(row[0])

    @staticmethod
    def _event(con, task, kind, payload):
        con.execute("INSERT INTO events(task_id,revision,event_type,payload,recorded_at) VALUES(?,?,?,?,?)",
                    (task["id"] if task else None, task["revision"] if task else None,
                     kind, encode(payload), time.time()))

    @staticmethod
    def _task(con, task_id):
        identifier(task_id, "task_id")
        row = con.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        require(row is not None, "Unknown task", "NOT_FOUND", task_id=task_id)
        task = dict(row)
        task["body"] = json.loads(task["body"])
        return task

    @staticmethod
    def _view(task):
        result = dict(task["body"])
        result.update({key: task[key] for key in
                       ("revision", "seq", "state", "attempts_used", "seconds_used", "seconds_reserved")})
        result.update(task_id=task["id"], stale=bool(task["stale"]))
        return result

    @staticmethod
    def _decode_row(row, fields):
        result = dict(row)
        for key in fields:
            if result.get(key) is not None:
                result[key] = json.loads(result[key])
        return result

    def status(self, task_id=None):
        con = None
        try:
            con = self._connect()
            con.execute("BEGIN")
            result = {"status": "OK", "project": self._project(con)}
            if task_id is None:
                result["tasks"] = [self._view(self._task(con, r[0]))
                                   for r in con.execute("SELECT id FROM tasks ORDER BY id")]
            else:
                task = self._task(con, task_id)
                result["task"] = self._view(task)
                for table, fields, order in (("claims", ["resources"], "generation"),
                                             ("attempts", ["binding", "receipt"], "rowid"),
                                             ("effects", ["body", "observation"], "rowid")):
                    result[table] = [self._decode_row(row, fields) for row in con.execute(
                        f"SELECT * FROM {table} WHERE task_id=? ORDER BY {order}", (task_id,))]
                result["events"] = [self._decode_row(row, ["payload"]) for row in con.execute(
                    "SELECT * FROM events WHERE task_id=? ORDER BY event_id", (task_id,))]
            con.execute("COMMIT")
            return result
        except sqlite3.Error as exc:
            raise StateError("STORAGE", "Cannot read state", {"sqlite": str(exc)}) from exc
        finally:
            if con is not None:
                con.close()

    def apply(self, request: dict) -> dict:
        obj(request, "request")
        request = json.loads(encode(request))
        op = text(request.get("op"), "op", 80)
        req_id = identifier(request.get("request_id"), "request_id")
        operations = {"register": self._register, "transition": self._transition,
                      "revise": self._revise, "adjust-budget": self._adjust_budget,
                      "update-candidate": self._update_candidate,
                      "claim": self._claim_new, "heartbeat": self._heartbeat,
                      "release": self._release, "begin-attempt": self._begin,
                      "finish-attempt": self._finish, "plan-effect": self._plan_effect,
                      "reconcile-effect": self._reconcile_effect, "resume": self._resume,
                      "backup": self._backup, "reconfigure": self._reconfigure}
        require(op in operations, "Unsupported operation", op=op)
        request_digest = digest(request)
        with self._transaction() as con:
            prior = con.execute("SELECT * FROM requests WHERE request_id=?", (req_id,)).fetchone()
            if prior:
                require(prior["request_digest"] == request_digest,
                        "Request id was reused with different content", "CONFLICT")
                result = json.loads(prior["result"])
                result["replayed"] = True
                return result
            task = None
            if op not in {"register", "backup", "reconfigure"}:
                task = self._task(con, request.get("task_id"))
                revision = integer(request.get("expected_revision"), "expected_revision", 1)
                seq = integer(request.get("expected_seq"), "expected_seq", 1)
                require(task["revision"] == revision and (task["seq"] == seq or
                        (op == "finish-attempt" and self._owner_heartbeats_since(con, request, task, seq))),
                        "Task revision or sequence changed", "CONFLICT",
                        current_revision=task["revision"], current_seq=task["seq"])
            specific = operations[op](con, request, task)
            if task is not None:
                con.execute("UPDATE tasks SET seq=seq+1 WHERE id=?", (task["id"],))
                task = self._task(con, task["id"])
                event = {"request_id": req_id, "request": request}
                if op == "adjust-budget":
                    event["adjustment"] = specific["budget_adjustment"]
                self._event(con, task, op, event)
            result = {"status": "OK", "op": op, **specific}
            if task is not None:
                result["task"] = self._view(task)
            con.execute("INSERT INTO requests VALUES (?,?,?)", (req_id, request_digest, encode(result)))
            return result

    @staticmethod
    def _owner_heartbeats_since(con, req, task, seq):
        """Permit receipt settlement across only this owner's lease renewals.

        Each task sequence advance has one durable event. Check the whole suffix
        in the same transaction as completion, including its original sequence
        comparisons; a control change or missing event cannot be skipped.
        """
        remaining = task["seq"] - seq
        if remaining <= 0:
            return False
        expected = task["seq"] - 1
        for event in con.execute("SELECT revision,event_type,payload FROM events WHERE task_id=? ORDER BY event_id DESC LIMIT ?",
                                 (task["id"], remaining)):
            prior = json.loads(event["payload"]).get("request", {})
            if (event["event_type"] != "heartbeat" or event["revision"] != task["revision"] or
                    prior.get("expected_revision") != task["revision"] or prior.get("expected_seq") != expected or
                    prior.get("worker_id") != req.get("worker_id") or prior.get("generation") != req.get("generation")):
                return False
            expected -= 1
            remaining -= 1
        return remaining == 0

    @staticmethod
    def _criteria(value):
        result, seen = [], set()
        for item in array(value, "criteria", True):
            obj(item, "criterion")
            cid = identifier(item.get("id"), "criterion.id")
            require(cid not in seen, "Duplicate criterion id")
            seen.add(cid)
            kind = item.get("kind")
            require(kind in {"test", "process", "review"}, "Invalid criterion kind")
            operations = strings(item.get("operation_ids"), "criterion.operation_ids", True)
            for operation in operations:
                identifier(operation, "operation_id")
            result.append({"id": cid, "kind": kind,
                           "description": text(item.get("description"), "criterion.description"),
                           "operation_ids": operations})
        return result

    def _dependencies(self, con, task_id, value):
        deps, seen = [], set()
        for item in array(value, "dependencies"):
            obj(item, "dependency")
            dep = identifier(item.get("task_id"), "dependency.task_id")
            revision = integer(item.get("revision"), "dependency.revision", 1)
            require(dep != task_id and dep not in seen, "Self or duplicate dependency")
            seen.add(dep)
            require(con.execute("SELECT 1 FROM revisions WHERE task_id=? AND revision=?",
                                (dep, revision)).fetchone(), "Unknown dependency revision", "NOT_FOUND")
            todo, visited = [dep], set()
            while todo:
                current = todo.pop()
                require(current != task_id, "Dependency cycle is not allowed", "CONFLICT")
                if current in visited:
                    continue
                visited.add(current)
                todo.extend(r[0] for r in con.execute(
                    "SELECT dep_id FROM dependencies WHERE task_id=?", (current,)))
            deps.append({"task_id": dep, "revision": revision})
        return deps

    @staticmethod
    def _set_dependencies(con, task_id, deps):
        con.execute("DELETE FROM dependencies WHERE task_id=?", (task_id,))
        con.executemany("INSERT INTO dependencies VALUES (?,?,?)",
                        [(task_id, d["task_id"], d["revision"]) for d in deps])

    @staticmethod
    def _budget(value):
        obj(value, "budget")
        return {"max_attempts": integer(value.get("max_attempts"), "budget.max_attempts", 1),
                "max_seconds": number(value.get("max_seconds"), "budget.max_seconds", positive=True)}

    @staticmethod
    def _workspace(value):
        path = Path(text(value, "workspace_root"))
        require(path.is_absolute() and path.is_dir(), "workspace_root must be an existing absolute directory")
        return str(path.resolve())

    def _register(self, con, req, task):
        task_id = identifier(req.get("task_id"), "task_id")
        require(not con.execute("SELECT 1 FROM tasks WHERE id=?", (task_id,)).fetchone(),
                "Task already exists; use its revision operations", "CONFLICT")
        endpoint = req.get("endpoint")
        require(endpoint in ENDPOINTS, "Invalid endpoint")
        profile = sha(req.get("profile_digest"), "profile_digest")
        require(profile == self._project(con)["profile_digest"],
                "Task profile differs from initialized project", "PROFILE")
        body = {"goal": text(req.get("goal"), "goal"), "endpoint": endpoint,
                "authority_ref": text(req.get("authority_ref"), "authority_ref"),
                "acceptor": text(req.get("acceptor"), "acceptor"),
                "candidate_digest": sha(req.get("candidate_digest"), "candidate_digest"),
                "profile_digest": profile, "criteria": self._criteria(req.get("criteria")),
                "workspace_root": self._workspace(req.get("workspace_root", self._project(con)["root"])),
                "dependencies": self._dependencies(con, task_id, req.get("dependencies", [])),
                "budget": self._budget(req.get("budget"))}
        con.execute("INSERT INTO tasks(id,revision,seq,state,body) VALUES(?,1,1,'clarifying',?)",
                    (task_id, encode(body)))
        con.execute("INSERT INTO revisions VALUES(?,1,?,'clarifying',NULL)", (task_id, encode(body)))
        self._set_dependencies(con, task_id, body["dependencies"])
        task = self._task(con, task_id)
        self._event(con, task, "register", {"request_id": req["request_id"], "body": body})
        return {"task": self._view(task)}

    def _deps_current(self, con, task):
        require(not task["stale"], "Task basis is stale; revise dependencies", "STALE")
        require(task["body"]["profile_digest"] == self._project(con)["profile_digest"],
                "Task operation profile is no longer current", "STALE")
        for dep in task["body"]["dependencies"]:
            actual = self._task(con, dep["task_id"])
            require(actual["revision"] == dep["revision"] and actual["state"] == "accepted"
                    and not actual["stale"], "Hard dependency is not current and accepted", "BLOCKED",
                    dependency=dep["task_id"], expected_revision=dep["revision"])

    @staticmethod
    def _basis(req, task):
        for key in ("candidate_digest", "profile_digest"):
            value = sha(req.get(key), key)
            require(value == task["body"][key], f"Current {key} differs", "STALE")

    @staticmethod
    def _no_pending(con, task):
        pending = con.execute("SELECT attempt_id FROM attempts WHERE task_id=? AND status IN ('pending','unknown')",
                              (task["id"],)).fetchall()
        require(not pending, "An attempt requires reconciliation", "BLOCKED",
                attempts=[r[0] for r in pending])

    @staticmethod
    def _effects_settled(con, task):
        unresolved = con.execute("SELECT effect_id,state FROM effects WHERE task_id=? AND ((revision=? AND state NOT IN ('succeeded','not_sent')) OR state IN ('planned','unknown','partial'))",
                                 (task["id"], task["revision"])).fetchall()
        require(not unresolved, "Material effects require reconciliation or remaining work", "BLOCKED",
                effects=[dict(r) for r in unresolved])

    @staticmethod
    def _no_uncertain_effects(con, task):
        uncertain = con.execute("SELECT effect_id,state FROM effects WHERE task_id=? AND state IN ('planned','unknown','partial')",
                                (task["id"],)).fetchall()
        require(not uncertain, "Reconcile unfinished material effects first", "BLOCKED",
                effects=[dict(r) for r in uncertain])

    def _latest_checks(self, con, task):
        wanted = {criterion["id"] for criterion in task["body"]["criteria"]}
        latest = {}
        for row in con.execute("SELECT * FROM attempts WHERE task_id=? AND revision=? AND category='check' ORDER BY rowid DESC",
                               (task["id"], task["revision"])):
            attempt = self._decode_row(row, ["binding", "receipt"])
            binding = attempt["binding"]
            if any(binding[key] != task["body"][key]
                   for key in ("candidate_digest", "profile_digest", "workspace_root")):
                continue
            for criterion in set(binding["criterion_ids"]) & wanted - set(latest):
                latest[criterion] = attempt
            if len(latest) == len(wanted):
                break
        return latest

    def _transition(self, con, req, task):
        to = req.get("to")
        text(req.get("reason"), "reason")
        require(to in STATES and task["state"] not in TERMINAL, "Invalid terminal/state transition", "CONFLICT")
        edges = {"clarifying": {"ready"}, "ready": {"running"},
                 "running": {"proposed"}, "proposed": {"running", "accepted"},
                 "blocked": {"ready", "running"}, "paused": {"ready", "running"}}
        allowed = edges.get(task["state"], set()) | {"blocked", "paused", "closed", "clarifying"}
        require(to in allowed and to != task["state"], "Transition is not allowed", "CONFLICT")
        if to in {"ready", "running", "proposed", "accepted"}:
            self._basis(req, task)
            self._deps_current(con, task)
        if task["state"] in {"blocked", "paused"} and to in {"ready", "running"}:
            text(req.get("resume_ref"), "resume_ref")
            self._no_pending(con, task)
        if to == "running":
            require(con.execute("SELECT 1 FROM claims WHERE task_id=? AND revision=? AND released=0 AND expires_at>?",
                                (task["id"], task["revision"], time.time())).fetchone(),
                    "Running requires a current resource claim", "BLOCKED")
        if to in {"proposed", "accepted", "closed", "clarifying"}:
            self._no_pending(con, task)
        acceptance = None
        if to == "accepted":
            self._effects_settled(con, task)
            require(req.get("acceptor") == task["body"]["acceptor"] and
                    req.get("authority_ref") == task["body"]["authority_ref"],
                    "Acceptance does not match the declared local authority", "AUTHORITY")
            evidence = obj(req.get("evidence"), "evidence")
            criteria = task["body"]["criteria"]
            require(set(evidence) == {c["id"] for c in criteria},
                    "Evidence must map every current criterion exactly")
            latest = self._latest_checks(con, task)
            for criterion in criteria:
                attempt = self._attempt(con, evidence[criterion["id"]], task)
                require(self._satisfies(attempt, task, criterion),
                        "Criterion lacks current successful observed evidence", "EVIDENCE",
                        criterion=criterion["id"], attempt_id=attempt["attempt_id"])
                require(latest.get(criterion["id"], {}).get("attempt_id") == attempt["attempt_id"],
                        "Later current evidence supersedes the selected attempt; reconcile and verify the criterion",
                        "EVIDENCE", criterion=criterion["id"], attempt_id=attempt["attempt_id"],
                        latest_attempt_id=latest.get(criterion["id"], {}).get("attempt_id"))
            acceptance = {"acceptor": req["acceptor"], "authority_ref": req["authority_ref"],
                          "authority_kind": "LOCAL_DECLARED", "evidence": evidence,
                          "candidate_digest": task["body"]["candidate_digest"],
                          "profile_digest": task["body"]["profile_digest"], "accepted_at": time.time()}
        con.execute("UPDATE tasks SET state=? WHERE id=?", (to, task["id"]))
        con.execute("UPDATE revisions SET body=?,last_state=?,acceptance=? WHERE task_id=? AND revision=?",
                    (encode(task["body"]), to, encode(acceptance) if acceptance else None,
                     task["id"], task["revision"]))
        return {"acceptance": acceptance} if acceptance else {}

    def _invalidate_downstream(self, con, parent):
        todo, seen = [parent], {parent}
        while todo:
            node = todo.pop()
            for row in con.execute("SELECT task_id FROM dependencies WHERE dep_id=?", (node,)).fetchall():
                child_id = row[0]
                if child_id in seen:
                    continue
                seen.add(child_id)
                todo.append(child_id)
                con.execute("UPDATE tasks SET stale=1,seq=seq+1 WHERE id=?", (child_id,))
                self._event(con, self._task(con, child_id), "dependency-invalidated", {"changed_task": parent})

    def _revise(self, con, req, task):
        text(req.get("reason"), "reason")
        require("budget" not in req, "Use adjust-budget with the owner's recorded authorization; revise preserves the budget")
        self._no_pending(con, task)
        require(not con.execute("SELECT 1 FROM claims WHERE task_id=? AND released=0", (task["id"],)).fetchone(),
                "Release observed stopped writers before revision", "BLOCKED")
        self._no_uncertain_effects(con, task)
        body = dict(task["body"])
        for key in ("goal", "authority_ref", "acceptor"):
            if key in req:
                body[key] = text(req[key], key)
        if "endpoint" in req:
            require(req["endpoint"] in ENDPOINTS, "Invalid endpoint")
            body["endpoint"] = req["endpoint"]
        if "criteria" in req:
            body["criteria"] = self._criteria(req["criteria"])
        body["candidate_digest"] = sha(req.get("candidate_digest"), "candidate_digest")
        if "profile_digest" in req:
            body["profile_digest"] = sha(req["profile_digest"], "profile_digest")
        require(body["profile_digest"] == self._project(con)["profile_digest"],
                "Revision must use the current project operation profile", "PROFILE")
        body["dependencies"] = self._dependencies(con, task["id"], req.get("dependencies", body["dependencies"]))
        con.execute("UPDATE revisions SET body=?,last_state=? WHERE task_id=? AND revision=?",
                    (encode(task["body"]), task["state"], task["id"], task["revision"]))
        con.execute("UPDATE tasks SET revision=revision+1,state='clarifying',stale=0,body=? WHERE id=?",
                    (encode(body), task["id"]))
        con.execute("INSERT INTO revisions VALUES(?,?,?,'clarifying',NULL)",
                    (task["id"], task["revision"] + 1, encode(body)))
        self._set_dependencies(con, task["id"], body["dependencies"])
        self._invalidate_downstream(con, task["id"])
        return {}

    def _adjust_budget(self, con, req, task):
        require(task["state"] not in TERMINAL, "Revise terminal work before adjusting its budget", "CONFLICT")
        text(req.get("reason"), "reason")
        decision = text(req.get("decision_ref"), "decision_ref")
        owner = text(req.get("authorized_by"), "authorized_by")
        authority = text(req.get("authority_ref"), "authority_ref")
        require(owner == task["body"]["acceptor"] and authority == task["body"]["authority_ref"],
                "Budget adjustment requires the declared owner's current authority", "AUTHORITY")
        budget = self._budget(req.get("budget"))
        previous = task["body"]["budget"]
        require(budget != previous, "Budget is unchanged", "CONFLICT")
        require(budget["max_attempts"] >= task["attempts_used"] and
                budget["max_seconds"] >= task["seconds_used"] + task["seconds_reserved"],
                "Budget cannot exclude consumed or reserved resources", "BUDGET")
        adjustment = {"before": previous, "after": budget, "authorized_by": owner,
                      "authority_ref": authority, "decision_ref": decision, "authority_kind": "LOCAL_DECLARED",
                      "attempts_used": task["attempts_used"], "seconds_used": task["seconds_used"],
                      "seconds_reserved": task["seconds_reserved"]}
        body = {**task["body"], "budget": budget}
        con.execute("UPDATE tasks SET body=? WHERE id=?", (encode(body), task["id"]))
        con.execute("UPDATE revisions SET body=? WHERE task_id=? AND revision=?",
                    (encode(body), task["id"], task["revision"]))
        return {"budget_adjustment": adjustment}

    def _update_candidate(self, con, req, task):
        text(req.get("reason"), "reason")
        require(task["state"] not in TERMINAL, "A terminal revision requires revise", "CONFLICT")
        self._no_pending(con, task)
        value = sha(req.get("candidate_digest"), "candidate_digest")
        body = {**task["body"], "candidate_digest": value}
        if "workspace_root" in req:
            body["workspace_root"] = self._workspace(req["workspace_root"])
            text(req.get("assignment_ref"), "assignment_ref")
        require(value != task["body"]["candidate_digest"] or body["workspace_root"] != task["body"]["workspace_root"],
                "Candidate and workspace have not changed", "CONFLICT")
        claim_result = {}
        if body["workspace_root"] != task["body"]["workspace_root"]:
            claim_result = self._move_workspace_claim(con, task, body["workspace_root"])
        state = "running" if task["state"] == "proposed" else task["state"]
        con.execute("UPDATE tasks SET body=?,state=? WHERE id=?", (encode(body), state, task["id"]))
        con.execute("UPDATE revisions SET body=?,last_state=? WHERE task_id=? AND revision=?",
                    (encode(body), state, task["id"], task["revision"]))
        self._invalidate_downstream(con, task["id"])
        return claim_result

    @staticmethod
    def _lease(req):
        seconds = number(req.get("lease_seconds"), "lease_seconds", positive=True)
        require(seconds <= 86400, "Lease exceeds one day")
        return seconds

    def _claim_new(self, con, req, task):
        require(task["state"] not in TERMINAL, "Terminal tasks cannot claim writers", "CONFLICT")
        worker = identifier(req.get("worker_id"), "worker_id")
        access = req.get("access", "write")
        require(access in {"read", "write"}, "Claim access must be read or write")
        resources = strings(req.get("resources"), "resources", access == "write")
        require(not any(r.startswith(WORKSPACE_RESOURCE_PREFIX) for r in resources),
                "workspace: resource names are reserved for automatic workspace ownership")
        resources = sorted(resources + [self._workspace_resource(task["body"]["workspace_root"])])
        lease = self._lease(req)
        self._resources_available(con, task["id"], resources, access)
        cur = con.execute("INSERT INTO claims(task_id,revision,worker_id,access,resources,expires_at) VALUES(?,?,?,?,?,?)",
                          (task["id"], task["revision"], worker, access, encode(resources), time.time() + lease))
        row = con.execute("SELECT * FROM claims WHERE generation=?", (cur.lastrowid,)).fetchone()
        return {"claim": self._decode_row(row, ["resources"]), "generation": cur.lastrowid}

    @staticmethod
    def _workspace_resource(workspace_root):
        return WORKSPACE_RESOURCE_PREFIX + str(Path(workspace_root).resolve())

    @staticmethod
    def _workspace_overlap(left, right, project_root):
        if left == right:
            return True
        parent, child = (left, right) if left in right.parents else (right, left)
        if parent not in child.parents:
            return False
        # The project root owns product files, while separate worktrees live in
        # its excluded administrative subtree. Their own nested claims still
        # overlap normally, including a reservation of the worktrees container.
        worktrees = project_root / ".contour" / "worktrees"
        return not (parent == project_root and worktrees in child.parents)

    def _resources_available(self, con, task_id, resources, access, exclude_generation=None):
        project_root = Path(self._project(con)["root"]).resolve()
        workspaces = [Path(r[len(WORKSPACE_RESOURCE_PREFIX):]).resolve() for r in resources
                      if r.startswith(WORKSPACE_RESOURCE_PREFIX)]
        named = {r for r in resources if not r.startswith(WORKSPACE_RESOURCE_PREFIX)}
        for prior in con.execute("SELECT * FROM claims WHERE released=0"):
            if prior["generation"] == exclude_generation:
                continue
            held = json.loads(prior["resources"])
            conflict = named & set(held)
            if access == "write" or prior["access"] == "write":
                for resource in held:
                    if resource.startswith(WORKSPACE_RESOURCE_PREFIX):
                        path = Path(resource[len(WORKSPACE_RESOURCE_PREFIX):]).resolve()
                        if any(self._workspace_overlap(path, root, project_root) for root in workspaces):
                            conflict.add(resource)
            require(prior["task_id"] != task_id and not conflict,
                    "Resource remains owned, including expired claims", "CONFLICT",
                    generation=prior["generation"], worker_id=prior["worker_id"], resources=sorted(conflict))

    def _move_workspace_claim(self, con, task, workspace_root):
        rows = con.execute("SELECT * FROM claims WHERE task_id=? AND released=0", (task["id"],)).fetchall()
        require(len(rows) <= 1, "Task has incompatible multiple owners", "SCHEMA")
        new_resource = self._workspace_resource(workspace_root)
        if not rows:
            self._resources_available(con, task["id"], [new_resource], "write")
            return {}
        claim = rows[0]
        old_resource = self._workspace_resource(task["body"]["workspace_root"])
        resources = json.loads(claim["resources"])
        require(old_resource in resources, "Claim lacks its automatic workspace reservation", "SCHEMA")
        moved = sorted({r for r in resources if r != old_resource} | {new_resource})
        self._resources_available(con, task["id"], moved, claim["access"], claim["generation"])
        con.execute("UPDATE claims SET resources=? WHERE generation=?", (encode(moved), claim["generation"]))
        row = con.execute("SELECT * FROM claims WHERE generation=?", (claim["generation"],)).fetchone()
        return {"claim": self._decode_row(row, ["resources"])}

    @staticmethod
    def _ownership(con, req, task, allow_expired=False):
        generation = integer(req.get("generation"), "generation", 1)
        worker = identifier(req.get("worker_id"), "worker_id")
        row = con.execute("SELECT * FROM claims WHERE generation=?", (generation,)).fetchone()
        require(row is not None and not row["released"] and row["task_id"] == task["id"]
                and row["revision"] == task["revision"] and row["worker_id"] == worker,
                "Stale or unrelated writer generation", "FENCED")
        if not allow_expired:
            require(row["expires_at"] > time.time(), "Writer lease expired; observe and renew it", "BLOCKED")
        return dict(row)

    def _heartbeat(self, con, req, task):
        claim = self._ownership(con, req, task, allow_expired=True)
        if claim["expires_at"] <= time.time():
            text(req.get("observation_ref"), "observation_ref for expired owner")
        con.execute("UPDATE claims SET expires_at=? WHERE generation=?",
                    (time.time() + self._lease(req), claim["generation"]))
        row = con.execute("SELECT * FROM claims WHERE generation=?", (claim["generation"],)).fetchone()
        return {"claim": self._decode_row(row, ["resources"])}

    def _release(self, con, req, task):
        claim = self._ownership(con, req, task, allow_expired=True)
        self._no_pending(con, task)
        stopped = text(req.get("stopped_ref"), "stopped_ref")
        con.execute("UPDATE claims SET released=1,stopped_ref=? WHERE generation=?", (stopped, claim["generation"]))
        row = con.execute("SELECT * FROM claims WHERE generation=?", (claim["generation"],)).fetchone()
        return {"claim": self._decode_row(row, ["resources"])}

    def _attempt(self, con, attempt_id, task):
        identifier(attempt_id, "attempt_id")
        row = con.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
        require(row is not None and row["task_id"] == task["id"], "Unknown task attempt", "NOT_FOUND")
        return self._decode_row(row, ["binding", "receipt"])

    def _effect(self, con, effect_id, task):
        identifier(effect_id, "effect_id")
        row = con.execute("SELECT * FROM effects WHERE effect_id=?", (effect_id,)).fetchone()
        require(row is not None and row["task_id"] == task["id"], "Unknown task effect", "NOT_FOUND")
        return self._decode_row(row, ["body", "observation"])

    def _begin(self, con, req, task):
        require(task["state"] == "running", "Attempts require running state", "BLOCKED")
        self._basis(req, task)
        self._deps_current(con, task)
        claim = self._ownership(con, req, task)
        self._resources_available(con, task["id"], json.loads(claim["resources"]), claim["access"], claim["generation"])
        self._no_pending(con, task)
        operation = identifier(req.get("operation_id"), "operation_id")
        category = req.get("category", "check")
        require(category in {"check", "worker", "integration"}, "Invalid attempt category")
        require("workspace_destination" not in req,
                "Create workspaces with host/project tools and assign the existing workspace explicitly")
        criteria = strings(req.get("criterion_ids", []), "criterion_ids", category == "check")
        by_id = {c["id"]: c for c in task["body"]["criteria"]}
        require(set(criteria) <= set(by_id), "Attempt names unknown criteria")
        for cid in criteria:
            require(operation in by_id[cid]["operation_ids"], "Operation does not map to criterion", criterion=cid)
        reserve = number(req.get("reserve_seconds"), "reserve_seconds", positive=True)
        budget = task["body"]["budget"]
        require(task["attempts_used"] < budget["max_attempts"] and
                task["seconds_used"] + task["seconds_reserved"] + reserve <= budget["max_seconds"] + 1e-9,
                "Aggregate execution budget is exhausted", "BUDGET")
        prior = con.execute("SELECT * FROM attempts WHERE task_id=? AND revision=? AND operation_id=? ORDER BY rowid DESC LIMIT 1",
                            (task["id"], task["revision"], operation)).fetchone()
        if prior:
            previous = self._decode_row(prior, ["binding", "receipt"])
            same_basis = all(previous["binding"][k] == task["body"][k]
                             for k in ("candidate_digest", "profile_digest", "workspace_root"))
            if same_basis and not self._receipt_pass(previous):
                text(req.get("resolution_ref"), "resolution_ref for failed same-basis retry")
        if criteria:
            latest = self._latest_checks(con, task)
            if any(cid in latest and not self._receipt_pass(latest[cid]) for cid in criteria):
                text(req.get("resolution_ref"), "resolution_ref for contradictory current criterion evidence")
        effect_id = req.get("effect_id")
        if effect_id is not None:
            effect = self._effect(con, effect_id, task)
            require(effect["revision"] == task["revision"], "Effect belongs to an old revision", "STALE")
            require(effect["state"] != "succeeded", "An observed successful effect cannot be repeated", "CONFLICT")
            if effect["state"] != "planned":
                observation = effect["observation"] or {}
                require(observation.get("observation_ref") == req.get("resolution_ref") and
                        bool(req.get("resolution_ref")), "Effect retry requires its recorded reconciliation", "BLOCKED")
                if effect["state"] in {"unknown", "partial"}:
                    idem = effect["body"].get("idempotency") or {}
                    require(bool(idem.get("verified_ref")) and idem.get("expires_at", 0) > time.time()
                            and req.get("retry_authority_ref") == effect["body"]["authority_ref"],
                            "Unknown effect lacks a current verified receiver retry guarantee", "BLOCKED")
                    text(req["retry_authority_ref"], "retry_authority_ref")
        attempt_id = "attempt-" + uuid.uuid4().hex
        binding = {"project_id": self._project(con)["project_id"], "task_id": task["id"],
                   "revision": task["revision"], "candidate_digest": task["body"]["candidate_digest"],
                   "workspace_root": task["body"]["workspace_root"],
                   "profile_digest": task["body"]["profile_digest"], "criterion_ids": sorted(criteria),
                   "worker_id": claim["worker_id"], "generation": claim["generation"],
                   "access": claim["access"],
                   "operation_id": operation, "attempt_id": attempt_id}
        if effect_id is not None:
            binding.update(effect_id=effect_id, effect_digest=digest(effect["body"]))
        con.execute("INSERT INTO attempts(attempt_id,task_id,revision,generation,worker_id,operation_id,category,status,reserve_seconds,binding,resolution_ref,effect_id) VALUES(?,?,?,?,?,?,?,'pending',?,?,?,?)",
                    (attempt_id, task["id"], task["revision"], claim["generation"], claim["worker_id"],
                     operation, category, reserve, encode(binding), req.get("resolution_ref"), effect_id))
        con.execute("UPDATE tasks SET attempts_used=attempts_used+1,seconds_reserved=seconds_reserved+? WHERE id=?",
                    (reserve, task["id"]))
        if effect_id is not None:
            con.execute("UPDATE effects SET state='unknown',attempt_id=? WHERE effect_id=?", (attempt_id, effect_id))
        attempt = self._attempt(con, attempt_id, task)
        return {"attempt_id": attempt_id, "binding": binding, "attempt": attempt}

    @staticmethod
    def _validate_receipt(receipt, attempt):
        obj(receipt, "receipt")
        require(type(receipt.get("schema_version")) is int and receipt["schema_version"] == 1,
                "Receipt schema_version must be 1")
        binding = obj(receipt.get("binding"), "receipt.binding")
        require(all(binding.get(k) == v for k, v in attempt["binding"].items()),
                "Receipt binding differs from durable attempt", "CONFLICT")
        require(type(receipt.get("completed")) is bool and
                receipt.get("status") in {"PASS", "FAIL", "INCOMPLETE", "ERROR"}, "Invalid receipt outcome")
        number(receipt.get("duration_seconds"), "receipt.duration_seconds")
        if attempt["category"] in {"worker", "integration"}:
            require(receipt.get("origin") == "HOST_OBSERVED" and receipt.get("kind") == attempt["category"],
                    "Worker/integration receipts must retain host-observed origin")
            text(receipt.get("witness_ref"), "receipt.witness_ref")
            return
        kind = receipt.get("kind")
        if kind == "review":
            require(receipt.get("origin") == "HOST_OBSERVED", "Review must be host-observed")
            text(receipt.get("witness_ref"), "receipt.witness_ref")
        else:
            require(receipt.get("origin") == "LOCAL_OBSERVED" and kind in {"unittest", "junit", "process"},
                    "Check receipt has unsupported origin or kind")
            require(receipt.get("returncode") is None or type(receipt.get("returncode")) is int,
                    "Invalid returncode")
            passed = receipt["status"] == "PASS"
            for argument in array(receipt.get("argv"), "receipt.argv", passed):
                require(isinstance(argument, str) and len(argument) <= 16384 and "\x00" not in argument,
                        "Invalid receipt argv argument")
            if passed or receipt.get("cwd") is not None:
                text(receipt.get("cwd"), "receipt.cwd")
            for log in array(receipt.get("logs"), "receipt.logs", passed):
                obj(log, "log")
                text(log.get("path"), "log.path")
                sha(log.get("sha256"), "log.sha256")
                integer(log.get("bytes"), "log.bytes")
            if kind in {"unittest", "junit"}:
                if passed or receipt.get("tests") is not None:
                    tests = obj(receipt.get("tests"), "receipt.tests")
                    for key in ("selected", "succeeded", "failed", "errors", "skipped",
                                "expected_failures", "unexpected_successes"):
                        integer(tests.get(key), f"receipt.tests.{key}")
            else:
                require(receipt.get("tests") is None, "Process receipt cannot assert test counts")
        for key in ("candidate_before", "candidate_after"):
            if receipt["status"] == "PASS" or receipt.get(key) is not None:
                sha(receipt.get(key), f"receipt.{key}")

    @staticmethod
    def _receipt_pass(attempt):
        receipt = attempt.get("receipt") or {}
        if attempt["status"] != "finished" or receipt.get("completed") is not True or receipt.get("status") != "PASS":
            return False
        if attempt["category"] != "check":
            return True
        candidate = attempt["binding"]["candidate_digest"]
        if receipt.get("candidate_before") != candidate or receipt.get("candidate_after") != candidate:
            return False
        kind = receipt.get("kind")
        if kind == "review":
            return receipt.get("origin") == "HOST_OBSERVED" and bool(receipt.get("witness_ref"))
        if receipt.get("origin") != "LOCAL_OBSERVED" or receipt.get("returncode") != 0:
            return False
        if kind in {"unittest", "junit"}:
            tests = receipt.get("tests") or {}
            return (tests.get("selected", 0) > 0 and tests.get("succeeded", 0) > 0 and
                    all(tests.get(k) == 0 for k in ("failed", "errors", "unexpected_successes")) and
                    sum(tests.get(k, 0) for k in ("succeeded", "failed", "errors", "skipped",
                                                "expected_failures", "unexpected_successes")) == tests["selected"])
        return kind == "process"

    def _satisfies(self, attempt, task, criterion):
        if attempt["category"] != "check" or not self._receipt_pass(attempt):
            return False
        binding = attempt["binding"]
        if (attempt["revision"] != task["revision"] or
                any(binding[k] != task["body"][k] for k in ("candidate_digest", "profile_digest", "workspace_root")) or
                criterion["id"] not in binding["criterion_ids"] or
                attempt["operation_id"] not in criterion["operation_ids"]):
            return False
        return attempt["receipt"]["kind"] in {"test": {"unittest", "junit"},
                                                "process": {"process"}, "review": {"review"}}[criterion["kind"]]

    def _finish(self, con, req, task):
        self._ownership(con, req, task, allow_expired=True)
        attempt = self._attempt(con, req.get("attempt_id"), task)
        require(attempt["revision"] == task["revision"] and attempt["generation"] == req["generation"]
                and attempt["worker_id"] == req["worker_id"], "Attempt belongs to a stale writer", "FENCED")
        receipt = req.get("receipt")
        self._validate_receipt(receipt, attempt)
        same = attempt["receipt"] is not None and encode(attempt["receipt"]) == encode(receipt)
        if same:
            return {"attempt": attempt, "duplicate_receipt": True}
        complete = receipt["completed"]
        duration = number(receipt["duration_seconds"], "duration_seconds")
        later_completion = (complete and attempt["status"] in {"unknown", "stopped"} and
                            (attempt["receipt"] is None or not attempt["receipt"]["completed"]))
        if later_completion and (attempt["receipt"] is not None or attempt["status"] == "stopped"):
            text(req.get("resolution_ref"), "resolution_ref for later observed completion")
            require(duration >= (attempt["actual_seconds"] or 0),
                    "Later completion cannot erase observed execution time", "CONFLICT")
        else:
            require(attempt["receipt"] is None and attempt["status"] in PENDING,
                    "Conflicting result for a completed attempt", "CONFLICT")
        status = "finished" if complete else "unknown"
        con.execute("UPDATE attempts SET status=?,actual_seconds=?,receipt=? WHERE attempt_id=?",
                    (status, duration, encode(receipt), attempt["attempt_id"]))
        if complete:
            if attempt["status"] == "stopped":
                already_charged = max(attempt["reserve_seconds"], attempt["actual_seconds"] or 0)
                con.execute("UPDATE tasks SET seconds_used=seconds_used+? WHERE id=?",
                            (max(0, duration - already_charged), task["id"]))
            else:
                con.execute("UPDATE tasks SET seconds_reserved=MAX(0,seconds_reserved-?),seconds_used=seconds_used+? WHERE id=?",
                            (attempt["reserve_seconds"], duration, task["id"]))
        return {"attempt": self._attempt(con, attempt["attempt_id"], task),
                "evidence_kind": "LOCAL_OBSERVED_NOT_PROTECTED" if receipt["origin"] == "LOCAL_OBSERVED"
                else "HOST_OBSERVED_NOT_PROTECTED"}

    def _plan_effect(self, con, req, task):
        require(task["state"] == "running", "Effect planning requires running state", "BLOCKED")
        self._ownership(con, req, task)
        self._deps_current(con, task)
        effect_id = identifier(req.get("effect_id"), "effect_id")
        require(not con.execute("SELECT 1 FROM effects WHERE effect_id=?", (effect_id,)).fetchone(),
                "Logical effect already exists", "CONFLICT")
        body = {k: text(req.get(k), k) for k in
                ("kind", "target", "environment", "authority_ref", "expected_result", "reconcile_method")}
        require(body["authority_ref"] == task["body"]["authority_ref"],
                "Effect authority does not match current declared authority", "AUTHORITY")
        body["parameters"] = obj(req.get("parameters"), "parameters")
        if "idempotency" in req:
            idem = obj(req["idempotency"], "idempotency")
            body["idempotency"] = {"key": text(idem.get("key"), "idempotency.key"),
                                   "verified_ref": text(idem.get("verified_ref"), "idempotency.verified_ref"),
                                   "expires_at": number(idem.get("expires_at"), "idempotency.expires_at", positive=True),
                                   "scope": text(idem.get("scope"), "idempotency.scope")}
        con.execute("INSERT INTO effects VALUES(?,?,?,'planned',?,NULL,NULL)",
                    (effect_id, task["id"], task["revision"], encode(body)))
        return {"effect": self._effect(con, effect_id, task)}

    def _reconcile_effect(self, con, req, task):
        effect = self._effect(con, req.get("effect_id"), task)
        outcome = req.get("outcome")
        require(outcome in {"succeeded", "failed", "partial", "unknown", "not_sent"}, "Invalid effect observation")
        require(effect["state"] != "succeeded" or outcome == "succeeded",
                "Do not erase an observed successful effect; record compensation separately", "CONFLICT")
        observation = {"outcome": outcome, "observation_ref": text(req.get("observation_ref"), "observation_ref"),
                       "details": text(req.get("details"), "details"), "observed_at": time.time()}
        con.execute("UPDATE effects SET state=?,observation=? WHERE effect_id=?",
                    (outcome, encode(observation), effect["effect_id"]))
        return {"effect": self._effect(con, effect["effect_id"], task)}

    def _resume(self, con, req, task):
        observations = obj(req.get("observations"), "observations")
        require(bool(observations) and len(observations) <= MAX_ITEMS, "Resume requires actual attempt observations")
        reconciled = []
        for attempt_id, observation in observations.items():
            obj(observation, "observation")
            attempt = self._attempt(con, attempt_id, task)
            require(attempt["status"] in PENDING, "Only incomplete attempts need resume observations", "CONFLICT")
            outcome = observation.get("outcome")
            require(outcome in {"stopped", "running", "unknown"}, "Invalid process observation")
            text(observation.get("observation_ref"), "observation_ref")
            if outcome == "stopped":
                con.execute("UPDATE attempts SET status='stopped',resolution_ref=? WHERE attempt_id=?",
                            (observation["observation_ref"], attempt_id))
                con.execute("UPDATE tasks SET seconds_reserved=MAX(0,seconds_reserved-?),seconds_used=seconds_used+? WHERE id=?",
                            (attempt["reserve_seconds"], max(attempt["reserve_seconds"],
                             attempt["actual_seconds"] or 0), task["id"]))
            elif outcome == "unknown":
                con.execute("UPDATE attempts SET status='unknown' WHERE attempt_id=?", (attempt_id,))
            reconciled.append({"attempt_id": attempt_id, **observation})
        return {"observations": reconciled, "automatic_replay": False}

    def _backup(self, con, req, task):
        try:
            destination = new_control_location(self._project(con)["root"], text(req.get("destination"), "destination"))
        except Invalid as error:
            raise StateError("CONFLICT", str(error)) from error
        fd = None
        source = target = None
        created = False
        try:
            fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            created = True
            os.close(fd)
            fd = None
            source = sqlite3.connect(self.db, timeout=2)
            target = sqlite3.connect(destination)
            deadline = time.monotonic() + 15
            def progress(status, remaining, total):
                require(time.monotonic() <= deadline, "Backup time budget exceeded", "BUDGET")
            source.backup(target, pages=128, progress=progress, sleep=0.01)
            require(target.execute("PRAGMA quick_check").fetchone()[0] == "ok", "Backup integrity check failed", "STORAGE")
            target.close()
            target = None
            self._event(con, None, "backup", {"destination": str(destination), "request_id": req["request_id"]})
            return {"destination": str(destination), "scope": "Consistent database snapshot before backup event"}
        except BaseException:
            if target is not None:
                target.close()
                target = None
            if created:
                destination.unlink(missing_ok=True)
            raise
        finally:
            if fd is not None:
                os.close(fd)
            if source is not None:
                source.close()
            if target is not None:
                target.close()

    def _reconfigure(self, con, req, task):
        project = self._project(con)
        expected = sha(req.get("expected_profile_digest"), "expected_profile_digest")
        new = sha(req.get("new_profile_digest"), "new_profile_digest")
        require(project["profile_digest"] == expected and new != expected,
                "Profile comparison failed or profile did not change", "CONFLICT")
        text(req.get("reason"), "reason")
        text(req.get("authority_ref"), "authority_ref")
        require(not con.execute("SELECT 1 FROM claims WHERE released=0 LIMIT 1").fetchone(),
                "Release observed stopped workers before reconfiguration", "BLOCKED")
        require(not con.execute("SELECT 1 FROM attempts WHERE status IN ('pending','unknown') LIMIT 1").fetchone(),
                "Reconcile attempts before reconfiguration", "BLOCKED")
        require(not con.execute("SELECT 1 FROM effects WHERE state IN ('planned','unknown','partial') LIMIT 1").fetchone(),
                "Reconcile material effects before reconfiguration", "BLOCKED")
        project["profile_digest"] = new
        project["reconfigured_at"] = time.time()
        con.execute("UPDATE metadata SET value=? WHERE key='project'", (encode(project),))
        ids = [row[0] for row in con.execute("SELECT id FROM tasks ORDER BY id")]
        for task_id in ids:
            con.execute("UPDATE tasks SET stale=1,seq=seq+1 WHERE id=?", (task_id,))
            self._event(con, self._task(con, task_id), "profile-invalidated",
                        {"old_profile_digest": expected, "new_profile_digest": new})
        self._event(con, None, "reconfigure", {"request_id": req["request_id"],
                    "old_profile_digest": expected, "new_profile_digest": new,
                    "reason": req["reason"], "authority_ref": req["authority_ref"]})
        return {"project": project, "invalidated_tasks": ids, "authority_kind": "LOCAL_DECLARED"}

