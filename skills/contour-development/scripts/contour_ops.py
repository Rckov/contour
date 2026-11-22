#!/usr/bin/env python3
"""Contour 1.0: bounded local observations, not an authorization service.

Only doctor/snapshot invoke Git, with fixed argument vectors. No shell, eval,
network, code generation, test execution, or product-file mutation is provided.
All reports go to stdout; callers choose where to preserve them.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import time

VERSION = "1.0"
MAX_JSON = 16 * 1024 * 1024
MAX_FILE = 64 * 1024 * 1024
MAX_TOTAL = 256 * 1024 * 1024
MAX_FILES = 20000
STATUSES = {"PASS", "FAIL", "INCOMPLETE", "NOT_RUN", "INCONCLUSIVE"}
STATES = {"clarifying", "ready", "running", "proposed", "accepted", "blocked", "paused", "closed"}
ENDPOINTS = {"analysis", "demo", "patch", "integrated", "release-ready", "deployed", "observed"}
CONTOUR_DIR = ".contour"
ADMIN_EXCLUSIONS = [CONTOUR_DIR + "/"]


class Invalid(ValueError):
    pass


class CLIParser(argparse.ArgumentParser):
    def error(self, message):
        raise Invalid("arguments: " + message + "; use --help for supported arguments")


def require(condition, message):
    if not condition:
        raise Invalid(message)


def obj(value, name):
    require(isinstance(value, dict), f"{name}: expected object")
    return value


def string(value, name):
    require(isinstance(value, str) and bool(value.strip()), f"{name}: expected nonempty string")
    return value


def integer(value, name, minimum=0):
    require(type(value) is int and value >= minimum, f"{name}: expected integer >= {minimum}")
    return value


def boolean(value, name):
    require(type(value) is bool, f"{name}: expected boolean")
    return value


def array(value, name):
    require(isinstance(value, list), f"{name}: expected array")
    return value


def schema_one(value, name):
    require(type(value.get("schema_version")) is int and value["schema_version"] == 1,
            f"{name}: schema_version must be the integer 1")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def hex_digest(value, name):
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value), f"{name}: expected sha256")
    return value


def pairs_unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def no_constant(value):
    raise Invalid(f"non-finite JSON number: {value}")


def load_json(path):
    p = Path(path)
    require(p.is_file() and not p.is_symlink(), f"not a regular input file: {p}")
    with p.open("rb") as stream:
        data = stream.read(MAX_JSON + 1)
    require(len(data) <= MAX_JSON, "JSON input exceeds 16 MiB")
    return json.loads(data, object_pairs_hook=pairs_unique, parse_constant=no_constant)


def relative(value, allow_dot=False):
    string(value, "relative path")
    p = PurePosixPath(value)
    require("\\" not in value and "\0" not in value and not p.is_absolute(), "use a relative POSIX path")
    require(".." not in p.parts and ".git" not in p.parts, "parent traversal / .git is not an input")
    normalized = p.as_posix()
    require(normalized != "." or allow_dot, "a file path is required")
    return normalized


def product_relative(value, allow_dot=False):
    """The root administrative subtree is never a product input or write scope."""
    name = relative(value, allow_dot=allow_dot)
    parts = PurePosixPath(name).parts
    require(not parts or parts[0] != CONTOUR_DIR,
            "Root .contour is reserved for Contour state, not product inputs")
    return name


def storage_path(value):
    """Validate lexical ancestors before resolving a path used for local writes.

    This rejects accidental aliases and traversal; it is not a same-user race
    isolation mechanism. Missing descendants are allowed for exclusive creation.
    """
    path = Path(value).absolute()
    require(".." not in path.parts, "Parent traversal is not a storage path")
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        require(not current.is_symlink(), "Storage path must not contain symlinks: " + str(current))
        if current != path:
            require(not current.exists() or current.is_dir(),
                    "Storage ancestor must be a directory: " + str(current))
    return path.resolve()


def contour_directory(root, *, create=False):
    """Keep a local ignore policy without changing any existing user policy."""
    root = storage_path(root)
    require(root.is_dir(), "Project root must be a directory")
    directory = storage_path(root / CONTOUR_DIR)
    require(not directory.exists() or directory.is_dir(), "Root .contour must be a real directory")
    if not create:
        return directory
    ignore = storage_path(directory / ".gitignore")
    require(not ignore.exists() or ignore.is_file(), ".contour/.gitignore must be a regular file")
    directory.mkdir(mode=0o700, exist_ok=True)
    if not ignore.exists():
        fd = os.open(ignore, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(b"*\n")
            stream.flush()
            os.fsync(stream.fileno())
    for path in (directory, root):
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    return directory


def control_location(root, value):
    """Allow reserved project storage and compatible external workspaces only."""
    root, target = storage_path(root), storage_path(value)
    administrative = contour_directory(root)
    require(target != root and not root.is_relative_to(target),
            "Control storage cannot contain the project root")
    if target.is_relative_to(root):
        require(target != administrative and target.is_relative_to(administrative),
                "Control storage inside the project must be below root .contour")
        for ancestor in target.parents:
            if ancestor == administrative:
                break
            if (ancestor / ".git").exists():
                require(target.is_relative_to(ancestor / CONTOUR_DIR),
                        "Control storage cannot be inside a worktree's product files")
    return target


def new_control_location(root, value):
    target = control_location(root, value)
    require(not target.exists(), "The destination must not already exist")
    require(target.parent.is_dir(), "The destination parent must be an existing directory")
    return target




def safe_file(root, name):
    name = relative(name)
    p = root
    for component in PurePosixPath(name).parts:
        p = p / component
        require(not p.is_symlink(), f"symlink is not followed: {name}")
    require(p.is_file(), f"not a regular file: {name}")
    return p


def fixed_git(root, args, optional=False):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    command = ["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-C", str(root), *args]
    try:
        r = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=15, env=env, check=False)
    except FileNotFoundError:
        if optional:
            return None
        raise Invalid("Git is unavailable; use explicit --path selections")
    if r.returncode:
        if optional:
            return None
        raise Invalid("Git read failed; use repository root or explicit --path selections")
    require(len(r.stdout) <= MAX_JSON, "Git inventory exceeds 16 MiB")
    return r.stdout


def root_path(value):
    root = Path(value).resolve()
    require(root.is_dir(), "root is not a directory")
    return root


def doctor(args):
    root = root_path(args.root)
    top = fixed_git(root, ["rev-parse", "--show-toplevel"], optional=True)
    head = fixed_git(root, ["rev-parse", "--verify", "HEAD"], optional=True)
    names = ["AGENTS.md", "CLAUDE.md", "package.json", "pyproject.toml", "Cargo.toml",
             "go.mod", "Makefile", "justfile", "pom.xml", "build.gradle", ".gitignore"]
    found = [name for name in names if (root / name).is_file() and not (root / name).is_symlink()]
    return {"status": "OBSERVED", "root": str(root), "python": sys.version.split()[0],
            "git_repository": top is not None,
            "git_root_matches": bool(top and Path(os.fsdecode(top).strip()).resolve() == root),
            "head": head.decode().strip() if head else None, "root_files": found,
            "limitations": ["Root-level inventory only; read applicable parent/nested instructions separately.",
                            "Commands were not parsed or executed; tool rights and Context7 were not tested."]}


def selected_files(root, paths, deadline=None):
    deadline = deadline if deadline is not None else time.monotonic() + 30
    contour_directory(root)
    if not paths:
        top = fixed_git(root, ["rev-parse", "--show-toplevel"])
        require(Path(os.fsdecode(top).strip()).resolve() == root, "git-visible mode requires repository root")
        data = fixed_git(root, ["ls-files", "-z", "--cached", "--others", "--exclude-standard", "--",
                                ".", ":(top,exclude,literal).contour"])
        names = {os.fsdecode(n) for n in data.split(b"\0") if n}
        names = {n for n in names if PurePosixPath(n).parts[0] != CONTOUR_DIR
                 and ((root / n).exists() or (root / n).is_symlink())}
        require(len(names) <= MAX_FILES, "more than 20000 selected files; narrow scope")
        return sorted(names)
    names = set()
    for selected in paths:
        name = product_relative(selected, allow_dot=True)
        p = root if name == "." else root / name
        current = root
        for component in PurePosixPath(name).parts:
            current = current / component
            require(not current.is_symlink(), f"symlink selection is not followed: {name}")
        if not p.exists():
            continue  # Absence is observed, not silently replaced with a fabricated file.
        if p.is_symlink() or not p.is_dir():
            names.add(name)
        else:
            # Do not traverse symlink directories; report their presence instead.
            def walk_error(error):
                raise Invalid(f"directory discovery failed: {error}")
            for directory, dirs, files in os.walk(p, followlinks=False, onerror=walk_error):
                require(time.monotonic() <= deadline, "file discovery budget exceeded")
                at_root = Path(directory) == root
                dirs[:] = [d for d in dirs if d != ".git" and not (at_root and d == CONTOUR_DIR)]
                for d in list(dirs):
                    dp = Path(directory) / d
                    if dp.is_symlink():
                        names.add(dp.relative_to(root).as_posix())
                        dirs.remove(d)
                for filename in files:
                    if filename != ".git" and not (at_root and filename == CONTOUR_DIR):
                        names.add((Path(directory) / filename).relative_to(root).as_posix())
                require(len(names) <= MAX_FILES, "more than 20000 selected files; narrow scope")
    return sorted(names)


def hash_file(root, name, deadline=None):
    deadline = deadline if deadline is not None else time.monotonic() + 30
    p = safe_file(root, name)
    before = p.stat()
    require(stat.S_ISREG(before.st_mode), f"not a regular file: {name}")
    require(before.st_size <= MAX_FILE, f"file exceeds 64 MiB: {name}")
    h = hashlib.sha256()
    read = 0
    fd = os.open(p, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        require((before.st_dev, before.st_ino) == (opened.st_dev, opened.st_ino), f"file replaced: {name}")
        while chunk := stream.read(1024 * 1024):
            require(time.monotonic() <= deadline, "file read budget exceeded")
            read += len(chunk)
            require(read <= MAX_FILE, f"file grew beyond 64 MiB: {name}")
            h.update(chunk)
        after = os.fstat(stream.fileno())
    live = p.stat()
    signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_mode)
    require(signature(before) == signature(live) and signature(opened) == signature(after),
            f"file changed during read: {name}")
    # On Windows stat(path) and fstat(handle) can encode different metadata for
    # the same unchanged object. Compare each view to itself over the read,
    # and cross-check identity, size and modification time across both views.
    if os.name == "nt":
        require((before.st_size, before.st_mtime_ns) == (opened.st_size, opened.st_mtime_ns),
                f"file changed while opening: {name}")
    else:
        require(signature(before) == signature(opened), f"file changed while opening: {name}")
    return {"sha256": h.hexdigest(), "size": read, "executable": bool(before.st_mode & 0o111)}


def snapshot(args):
    root = root_path(args.root)
    string(args.project_id, "project id")
    paths = sorted({product_relative(p, allow_dot=True) for p in args.path})
    start = time.monotonic()
    names = selected_files(root, paths, start + 30)
    files, issues, total = {}, [], 0
    for name in names:
        try:
            if time.monotonic() - start > 30:
                issues.append("30-second observation budget exceeded; remaining files not read")
                break
            record = hash_file(root, name, start + 30)
            total += record["size"]
            require(total <= MAX_TOTAL, "256 MiB observation budget exceeded")
            files[relative(name)] = record
        except (Invalid, OSError) as exc:
            issues.append(str(exc))
            if total > MAX_TOTAL:
                break
    try:
        if names != selected_files(root, paths, start + 30):
            issues.append("selected file set changed during observation")
    except (Invalid, OSError) as exc:
        issues.append(str(exc))
    body = {"schema_version": 1, "project_id": args.project_id,
            "selection": {"mode": "explicit" if paths else "git-visible", "paths": paths,
                          "excluded_subtrees": list(ADMIN_EXCLUSIONS)}, "files": files}
    return {**body, "status": "INCOMPLETE" if issues else "OBSERVED", "digest": digest(body),
            "root": str(root), "issues": issues, "selected_count": len(names), "hashed_count": len(files),
            "absent_explicit_paths": [p for p in paths if not (root / p).exists()],
            "limitations": ["Observation is not an atomic/isolation snapshot.",
                            "Only root .contour/ is reserved administrative state; do not store product inputs there.",
                            "External state, environment and dependency behavior are not hashed.",
                            "Git-visible excludes ignored files and does not recurse into submodules; select relevant inputs explicitly."]}


def checked_snapshot(value):
    s = obj(value, "snapshot")
    schema_one(s, "snapshot")
    require(s.get("status") == "OBSERVED", "snapshot must be a complete observation")
    string(s.get("project_id"), "snapshot.project_id")
    files = obj(s.get("files"), "snapshot.files")
    require(len(files) <= MAX_FILES, "too many snapshot entries")
    for name, record in files.items():
        require(product_relative(name) == name, "noncanonical snapshot path")
        obj(record, name)
        hex_digest(record.get("sha256"), name)
        integer(record.get("size"), "size")
        boolean(record.get("executable"), "executable")
    selection = obj(s.get("selection"), "selection")
    require(selection.get("mode") in {"explicit", "git-visible"}, "unknown selection mode")
    paths = array(selection.get("paths"), "selection.paths")
    require(bool(paths) == (selection["mode"] == "explicit"), "selection mode/paths mismatch")
    for p in paths:
        product_relative(p, allow_dot=True)
    if "excluded_subtrees" in selection:
        require(selection["excluded_subtrees"] == ADMIN_EXCLUSIONS, "unsupported snapshot exclusion policy")
    body = {k: s[k] for k in ("schema_version", "project_id", "selection", "files")}
    require(s.get("digest") == digest(body), "snapshot digest mismatch")
    require(not s.get("issues"), "snapshot reports unresolved issues")
    integer(s.get("selected_count"), "selected_count")
    integer(s.get("hashed_count"), "hashed_count")
    require(s["selected_count"] == s["hashed_count"] == len(files), "snapshot count mismatch")
    return s


def matches(name, rule):
    prefix = rule.endswith("/")
    clean = relative(rule, allow_dot=True)
    return clean == "." or name == clean or (prefix and name.startswith(clean + "/"))


def scope(args):
    before, after = checked_snapshot(load_json(args.before)), checked_snapshot(load_json(args.after))
    require(before["project_id"] == after["project_id"] and before["selection"] == after["selection"],
            "scope observations must have the same project and selection policy")
    require(bool(args.allow), "provide at least one explicit --allow file or directory/")
    for rule in args.allow + args.protect:
        product_relative(rule, allow_dot=True)
    changes = []
    for name in sorted(before["files"].keys() | after["files"].keys()):
        old, new = before["files"].get(name), after["files"].get(name)
        if old != new:
            changes.append({"path": name, "kind": "added" if old is None else "deleted" if new is None else "modified"})
    outside = [r["path"] for r in changes if not any(matches(r["path"], rule) for rule in args.allow)]
    protected = [r["path"] for r in changes if any(matches(r["path"], rule) for rule in args.protect)]
    return {"status": "FAIL" if outside else "REVIEW_REQUIRED" if protected else "PASS",
            "changes": changes, "outside_scope": outside, "protected_changes": protected,
            "limitations": ["Only selected files are compared; semantic shared resources are not inferred.",
                            "PASS means path agreement, not authorization or behavior preservation."]}


def task(value):
    t = obj(value, "task")
    schema_one(t, "task")
    for field in ("id", "project_id", "goal", "authorization_ref", "acceptor", "environment_id"):
        string(t.get(field), f"task.{field}")
    integer(t.get("revision"), "task.revision", 1)
    require(t.get("state") in STATES and t.get("endpoint") in ENDPOINTS, "unknown task state/endpoint")
    criteria = array(t.get("criteria"), "task.criteria")
    require(bool(criteria), "at least one explicit acceptance criterion is required")
    ids = set()
    for c in criteria:
        obj(c, "criterion")
        cid = string(c.get("id"), "criterion.id")
        require(cid not in ids, "duplicate criterion id")
        ids.add(cid)
        integer(c.get("revision"), "criterion.revision", 1)
        string(c.get("description"), "criterion.description")
        require(c.get("method") in {"test", "inspection", "analysis", "demonstration"}, "unknown criterion method")
        boolean(c.get("waiver_allowed"), "criterion.waiver_allowed")
        for authority in array(c.get("waiver_authorities"), "criterion.waiver_authorities"):
            string(authority, "waiver authority")
        require(not c["waiver_allowed"] or bool(c["waiver_authorities"]), "waivable criterion needs authority")
    return t


def contract_digest(t):
    # Scope and all contextual task fields are included, apart from changing workflow state.
    return digest({k: v for k, v in t.items() if k not in {"state", "progress", "next_step"}})


def gate(args):
    t = task(load_json(args.task))
    s = checked_snapshot(load_json(args.snapshot))
    require(t["project_id"] == s["project_id"], "task/snapshot project mismatch")
    issues, observations, waived = [], [], []
    expected = {"task_id": t["id"], "task_revision": t["revision"], "contract_digest": contract_digest(t),
                "candidate_digest": s["digest"], "environment_id": t["environment_id"]}
    if not args.evidence:
        return {"status": "PREPARED", "admission": "NOT_DECIDED", "expected_binding": expected,
                "evidence_envelope": {"schema_version": 1, **expected, "records": [], "waivers": []},
                "limitations": ["This prepares identity fields before checks; it does not run or certify them.",
                                "Preserve this binding with actual primary results; never rebind an old result to a new candidate."]}
    e = obj(load_json(args.evidence), "evidence")
    schema_one(e, "evidence")
    integer(e.get("task_revision"), "evidence.task_revision", 1)
    for key, value in expected.items():
        if e.get(key) != value:
            issues.append(f"evidence {key} does not match current input")
    records, seen = array(e.get("records"), "evidence.records"), set()
    criteria = {c["id"]: c for c in t["criteria"]}
    by_criterion = {key: [] for key in criteria}
    refs = []
    for r in records:
        obj(r, "evidence record")
        rid = string(r.get("id"), "record.id")
        require(rid not in seen, "duplicate evidence record id")
        seen.add(rid)
        cid = string(r.get("criterion_id"), "record.criterion_id")
        require(cid in criteria, f"unknown criterion: {cid}")
        integer(r.get("criterion_revision"), "record.criterion_revision", 1)
        require(r.get("status") in STATUSES, "unknown evidence status")
        string(r.get("primary_ref"), "record.primary_ref")
        string(r.get("observed_at"), "record.observed_at")
        boolean(r.get("completed"), "record.completed")
        refs.append(r["primary_ref"])
        valid = (r.get("criterion_revision") == criteria[cid]["revision"]
                 and r.get("candidate_digest") == s["digest"]
                 and r.get("contract_digest") == expected["contract_digest"]
                 and r.get("environment_id") == expected["environment_id"]
                 and r.get("method") == criteria[cid]["method"])
        if r["status"] == "PASS":
            valid = valid and r["completed"]
            if r.get("method") == "test":
                valid = valid and type(r.get("selection_count")) is int and r["selection_count"] > 0
        by_criterion[cid].append((r, valid))
        observations.append({"id": rid, "criterion_id": cid, "reported_status": r["status"], "current_and_well_formed": valid})
    waivers = array(e.get("waivers", []), "waivers")
    waiver_map = {}
    for w in waivers:
        obj(w, "waiver")
        cid = string(w.get("criterion_id"), "waiver.criterion_id")
        require(cid in criteria and cid not in waiver_map, "unknown/duplicate waiver criterion")
        waiver_map[cid] = w
    now = datetime.now(timezone.utc)
    for cid, c in criteria.items():
        rs = by_criterion[cid]
        clean = bool(rs) and all(valid and r["status"] == "PASS" for r, valid in rs)
        if clean:
            continue
        w = waiver_map.get(cid)
        w_ok = False
        if w:
            integer(w.get("task_revision"), "waiver.task_revision", 1)
            for field in ("approved_by", "authorization_ref", "reason", "compensation", "expires_at"):
                string(w.get(field), f"waiver.{field}")
            expires = datetime.fromisoformat(w["expires_at"].replace("Z", "+00:00"))
            require(expires.tzinfo is not None, "waiver expiry requires timezone")
            w_ok = (c["waiver_allowed"] and w["approved_by"] in c["waiver_authorities"]
                    and expires > now and w.get("task_revision") == t["revision"]
                    and w.get("candidate_digest") == s["digest"]
                    and w.get("contract_digest") == expected["contract_digest"])
        if w_ok:
            waived.append(cid)
        else:
            issues.append(f"{cid}: absent, stale, incomplete or unsuccessful evidence; no current allowed waiver")
    return {"status": "FAIL" if issues else "PASS", "admission": "NOT_DECIDED",
            "evidence_truth_and_sufficiency": "NOT_ASSESSED", "issues": issues,
            "waived_criteria": waived, "observations": observations, "unverified_primary_refs": refs,
            "contract_digest": expected["contract_digest"],
            "limitations": ["Caller-provided identities/authority/evidence are not authenticated.",
                            "A reviewer must verify primary evidence, coverage, environment and endpoint.",
                            "PASS is mechanical consistency only; waivers do not change failed check facts."]}


def handoff(args):
    t = task(load_json(args.task))
    h = obj(load_json(args.record), "handoff")
    schema_one(h, "handoff")
    integer(h.get("task_revision"), "handoff.task_revision", 1)
    require(h.get("task_id") == t["id"] and h.get("task_revision") == t["revision"], "handoff task mismatch")
    for field in ("basis_ref", "next_step", "authorization_ref"):
        string(h.get(field), f"handoff.{field}")
    for field in ("artifact_refs", "evidence_refs", "unknowns", "owners", "active_processes", "effects"):
        array(h.get(field), f"handoff.{field}")
    effects = []
    for effect in h["effects"]:
        obj(effect, "effect")
        string(effect.get("id"), "effect.id")
        require(effect.get("status") in {"planned", "sent", "succeeded", "failed", "partial", "unknown"}, "unknown effect state")
        string(effect.get("reconcile_next"), "effect.reconcile_next")
        if effect["status"] in {"sent", "partial", "unknown"}:
            effects.append(effect["id"])
    budget = obj(h.get("budget"), "budget")
    for field in ("spent", "limit"):
        number(budget.get(field), f"budget.{field}")
    string(budget.get("unit"), "budget.unit")
    return {"status": "RECORDED", "task": {"id": t["id"], "revision": t["revision"], "state": t["state"],
                                              "goal": t["goal"], "endpoint": t["endpoint"]},
            "record": h, "effects_requiring_reconciliation": effects,
            "budget_exhausted": budget["spent"] >= budget["limit"],
            "resume_permission": "NOT_GRANTED", "limitations": ["Verify live state, actual writers and all effects before resuming."]}


def api_surface(args):
    def collect(path):
        d = obj(load_json(path), "OpenAPI document")
        version = string(d.get("openapi"), "openapi")
        require(re.fullmatch(r"3\.[012]\.\d+(?:[-+].*)?", version), "supported inventory: OpenAPI 3.0/3.1/3.2 JSON")
        paths = obj(d.get("paths", {}), "paths")
        operations, refs, issues = {}, [], []
        fixed_methods = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
        if version.startswith("3.2."):
            fixed_methods += ("query",)
        stack = [d]
        nodes = 0
        while stack:
            node = stack.pop()
            nodes += 1
            require(nodes <= 200000, "API node budget exceeded")
            if isinstance(node, dict):
                if "$ref" in node:
                    refs.append(node["$ref"])
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
        ids = set()
        for route, item in paths.items():
            if route.startswith("x-"):
                continue
            require(route.startswith("/"), "invalid path-item key")
            obj(item, "path item")
            named = [(method.upper(), item[method]) for method in fixed_methods if method in item]
            if "query" in item and "query" not in fixed_methods:
                issues.append("query field requires a supported 3.2 version: " + route)
            if "additionalOperations" in item:
                if version.startswith("3.2."):
                    additional = obj(item["additionalOperations"], "additionalOperations")
                    for method, op in additional.items():
                        require(re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", method), "invalid additional HTTP method token")
                        require(method not in {m.upper() for m in fixed_methods}, "additionalOperations duplicates a fixed HTTP method")
                        named.append((method, op))
                else:
                    issues.append("additionalOperations requires a supported 3.2 version: " + route)
            shared_path = {k: v for k, v in item.items() if k not in set(fixed_methods) | {"additionalOperations"}}
            for method, op in named:
                op = obj(op, "operation")
                oid = op.get("operationId")
                if oid is not None:
                    string(oid, "operationId")
                    if oid in ids:
                        issues.append("duplicate operationId: " + oid)
                    ids.add(oid)
                operations[method + " " + route] = {"operationId": oid, "definition_digest": digest({"operation": op, "path": shared_path})}
        return {"openapi": version, "document_digest": digest(d), "operations": operations,
                "references": refs, "issues": issues}
    current = collect(args.file)
    result = {"status": "ANALYSIS_ONLY", "current": current,
              "compatibility": "NOT_ASSESSED", "schema_validation": "NOT_PERFORMED",
              "coverage": "paths operations only; includes QUERY and additionalOperations for 3.2; no reference resolution",
              "limitations": ["JSON inventory only; references are never fetched/resolved.",
                              "Components, security, callbacks, webhooks and semantics require project validators/review."]}
    if args.before:
        previous = collect(args.before)
        a, b = previous["operations"], current["operations"]
        result["comparison"] = {"added": sorted(b.keys()-a.keys()), "removed": sorted(a.keys()-b.keys()),
                                "changed": sorted(k for k in a.keys() & b.keys() if a[k] != b[k]),
                                "document_changed": previous["document_digest"] != current["document_digest"]}
    return result


def number(value, name):
    require(type(value) in (int, float) and value >= 0, f"{name}: expected finite nonnegative number")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    require(finite, f"{name}: number exceeds the supported finite floating-point range")
    return value


def _eval_fields(value, fields, name):
    obj(value, name)
    fields = set(fields.split())
    require(set(value) == fields,
            f"{name}: missing fields {sorted(fields - set(value))}; unknown fields {sorted(set(value) - fields)}")


def _eval_ref(value, name):
    string(value, name)
    require(len(value) <= 4096, f"{name}: exceeds 4096 characters")
    return value


def _eval_ids(value, name):
    array(value, name)
    require(0 < len(value) <= MAX_FILES, f"{name}: expected 1 to {MAX_FILES} identities")
    for item in value:
        _eval_ref(item, name)
    require(len(set(value)) == len(value), f"{name}: duplicate identity")
    return set(value)


def _eval_add(metric, value, name, tokens=False):
    if value is None:
        metric["unknown_observations"] += 1
        return
    if tokens:
        integer(value, name)
    number(value, name)
    metric["known_subtotal"] += value
    number(metric["known_subtotal"], "aggregate." + name)
    metric["measured_observations"] += 1


def _eval_partial(d):
    """Summarize declared observations; do not obtain or authenticate telemetry."""
    _eval_fields(d, "schema_version currency variants units", "evaluation")
    _eval_ref(d["currency"], "currency")
    variants = _eval_ids(d["variants"], "variants")
    units = array(d["units"], "units")
    require(bool(units), "empty evaluation")
    require(len(units) * len(variants) <= MAX_FILES, "evaluation pairing budget exceeded")
    metric_names = ("total_tokens", "total_cost", "human_hours", "elapsed_seconds", "elapsed_seconds_sum")
    groups = {variant: {
        "units": 0, "accepted": 0, "not_accepted": 0, "acceptance_unknown": 0,
        "attempts": {"present": 0, "expected": 0, "missing_ids": [], "unexpected_ids": [],
                     "unfinished_ids": [], "unknown_inventory_units": []},
        "metrics": {name: {"known_subtotal": 0, "measured_observations": 0,
                            "unknown_observations": 0} for name in metric_names}}
        for variant in sorted(variants)}
    unit_ids, case_ids, source_refs = set(), set(), set()
    expected_owners, present_owners, cases = {}, {}, {}
    for u in units:
        _eval_fields(u, "id variant case_id repeat_id criteria_ref source_ref accepted expected_attempt_ids "
                     "attempts overhead_cost human_hours elapsed_seconds", "unit")
        for field in ("id", "variant", "case_id", "repeat_id", "criteria_ref", "source_ref"):
            _eval_ref(u[field], "unit." + field)
        variant = u["variant"]
        require(variant in variants, "unit names an undeclared variant")
        identity = (variant, u["id"])
        case = (u["case_id"], u["repeat_id"])
        require(identity not in unit_ids, "duplicate complete trajectory id within variant")
        require((variant, *case) not in case_ids, "duplicate case/repeat within variant")
        unit_ids.add(identity)
        case_ids.add((variant, *case))
        cases.setdefault(case, {})[variant] = u["criteria_ref"]
        accepted = u["accepted"]
        if accepted is not None:
            boolean(accepted, "accepted")
        g = groups[variant]
        g["units"] += 1
        g["acceptance_unknown" if accepted is None else "accepted" if accepted else "not_accepted"] += 1
        inventory = g["attempts"]
        expected = u["expected_attempt_ids"]
        if expected is None:
            inventory["expected"] = None
            inventory["unknown_inventory_units"].append(u["id"])
        else:
            expected = _eval_ids(expected, "expected_attempt_ids")
            require(not expected.intersection(expected_owners), "expected attempt belongs to more than one unit")
            expected_owners.update({attempt_id: identity for attempt_id in expected})
            require(len(expected_owners) <= MAX_FILES, "evaluation expected-attempt budget exceeded")
            if inventory["expected"] is not None:
                inventory["expected"] += len(expected)
        attempts = array(u["attempts"], "attempts")
        present = set()
        for attempt in attempts:
            _eval_fields(attempt, "id source_ref outcome total_tokens total_cost elapsed_seconds", "attempt")
            aid = _eval_ref(attempt["id"], "attempt.id")
            source = _eval_ref(attempt["source_ref"], "attempt.source_ref")
            require(aid not in present_owners, "duplicate attempt identity")
            require(source not in source_refs, "duplicate attempt source reference")
            require(attempt["outcome"] in ("completed", "failed", "cancelled", "incomplete", "unknown"),
                    "invalid attempt outcome")
            present_owners[aid] = identity
            source_refs.add(source)
            present.add(aid)
            require(len(present_owners) <= MAX_FILES, "evaluation observed-attempt budget exceeded")
            if attempt["outcome"] in ("incomplete", "unknown"):
                inventory["unfinished_ids"].append(aid)
            for field, metric in (("total_tokens", "total_tokens"), ("total_cost", "total_cost"),
                                  ("elapsed_seconds", "elapsed_seconds_sum")):
                _eval_add(g["metrics"][metric], attempt[field], "attempt." + field, field == "total_tokens")
        inventory["present"] += len(present)
        if expected is not None:
            missing = expected - present
            inventory["missing_ids"].extend(sorted(missing))
            inventory["unexpected_ids"].extend(sorted(present - expected))
            for metric in ("total_tokens", "total_cost", "elapsed_seconds_sum"):
                g["metrics"][metric]["unknown_observations"] += len(missing)
        for field, metric in (("overhead_cost", "total_cost"), ("human_hours", "human_hours"),
                              ("elapsed_seconds", "elapsed_seconds")):
            _eval_add(g["metrics"][metric], u[field], "unit." + field)
    require(all(expected_owners.get(aid, owner) == owner for aid, owner in present_owners.items()),
            "observed attempt belongs to another unit's declared inventory")
    base_reasons = {"caller_supplied_not_authenticated", "qualification_not_performed"}
    no_claim = set(base_reasons)
    for g in groups.values():
        inventory = g["attempts"]
        inventory["coverage_complete"] = bool(g["units"]) and not any(
            inventory[key] for key in ("missing_ids", "unexpected_ids", "unfinished_ids", "unknown_inventory_units"))
        reasons = set(base_reasons)
        if not inventory["coverage_complete"]:
            reasons.add("attempt_inventory_incomplete")
        if not g["units"]:
            reasons.add("no_units")
        for name, metric in g["metrics"].items():
            scope_complete = inventory["coverage_complete"] if name in (
                "total_tokens", "total_cost", "elapsed_seconds_sum") else bool(g["units"])
            metric["coverage_complete"] = scope_complete and not metric["unknown_observations"]
            metric["total"] = metric["known_subtotal"] if metric["coverage_complete"] else None
            if not metric["coverage_complete"]:
                reasons.add(name + "_incomplete")
        g["acceptance_rate"] = (g["accepted"] / g["units"]
                                if g["units"] and not g["acceptance_unknown"] else None)
        cost = g["metrics"]["total_cost"]["total"]
        g["cost_per_accepted"] = (cost / g["accepted"]
                                  if cost is not None and g["accepted"] and not g["acceptance_unknown"] else None)
        if g["acceptance_unknown"]:
            reasons.add("acceptance_unknown")
        if g["units"] and not g["accepted"] and not g["acceptance_unknown"]:
            reasons.add("zero_accepted")
        g["no_claim_reasons"] = sorted(reasons)
        no_claim.update(reasons)
    pairing = {"case_repeat_groups": len(cases), "keys_present_in_all_variants": 0,
               "unmatched": [], "criteria_mismatches": []}
    for (case_id, repeat_id), criteria in sorted(cases.items()):
        key = {"case_id": case_id, "repeat_id": repeat_id}
        missing = variants - set(criteria)
        if missing:
            pairing["unmatched"].append({**key, "missing_variants": sorted(missing)})
        else:
            pairing["keys_present_in_all_variants"] += 1
        if len(set(criteria.values())) > 1:
            pairing["criteria_mismatches"].append({**key, "criteria_refs": dict(sorted(criteria.items()))})
    if len(variants) < 2:
        no_claim.add("fewer_than_two_variants")
    if pairing["unmatched"]:
        no_claim.add("cases_unmatched")
    if pairing["criteria_mismatches"]:
        no_claim.add("criteria_mismatched")
    return {"status": "ANALYSIS_ONLY", "schema_version": 2, "currency": d["currency"],
            "data_assurance": "CALLER_SUPPLIED_NOT_AUTHENTICATED", "variants": groups,
            "pairing": pairing, "no_claim_reasons": sorted(no_claim),
            "limitations": [
                "Source references are caller-supplied labels; this operation does not read or authenticate them.",
                "Complete coverage means the declared inventory only; omitted attempts or agents are not ruled out.",
                "Each attempt supplies one non-overlapping recorded token total; cached tokens are not added or priced.",
                "Total cost includes attempt costs and allocated overhead; human hours are not silently monetized.",
                "elapsed_seconds sums declared whole-trajectory wall times; elapsed_seconds_sum sums attempt durations.",
                "Neither duration sum is campaign wall time when trajectories or attempts overlap.",
                "Matching case and criteria references does not establish quality equivalence or independent acceptance.",
                "Descriptive observations only; no billing inference, ranking, significance or superiority claim."]}


def eval_summary(args):
    d = obj(load_json(args.file), "evaluation")
    if type(d.get("schema_version")) is int and d["schema_version"] == 2:
        return _eval_partial(d)
    schema_one(d, "evaluation")
    string(d.get("currency"), "currency")
    units = array(d.get("units"), "units")
    require(bool(units), "empty evaluation")
    groups, seen = {}, set()
    for u in units:
        obj(u, "unit")
        identity = (string(u.get("variant"), "variant"), string(u.get("id"), "unit id"))
        require(identity not in seen, "duplicate complete trajectory id within variant")
        seen.add(identity)
        boolean(u.get("accepted"), "accepted")
        integer(u.get("attempts"), "attempts", 1)
        integer(u.get("total_tokens"), "total_tokens")
        for field in ("total_cost", "human_hours", "elapsed_seconds"):
            number(u.get(field), field)
        g = groups.setdefault(identity[0], {"units": 0, "accepted": 0, "attempts": 0, "cost": 0.0,
                                          "human_hours": 0.0, "total_tokens": 0, "elapsed_seconds_sum": 0.0})
        g["units"] += 1
        g["accepted"] += int(u["accepted"])
        g["attempts"] += u["attempts"]
        g["cost"] += u["total_cost"]
        g["human_hours"] += u["human_hours"]
        g["total_tokens"] += u["total_tokens"]
        g["elapsed_seconds_sum"] += u["elapsed_seconds"]
        for field in ("cost", "human_hours", "elapsed_seconds_sum"):
            number(g[field], "aggregate." + field)
    for g in groups.values():
        g["acceptance_rate"] = g["accepted"] / g["units"]
        g["cost_per_accepted"] = g["cost"] / g["accepted"] if g["accepted"] else None
    return {"status": "ANALYSIS_ONLY", "currency": d["currency"], "variants": groups,
            "limitations": ["Descriptive totals from caller data; no significance, quality equivalence or causal claim.",
                            "Each unit must contain all agents/attempts and allocated setup/maintenance costs.",
                            "Units are full trajectories, not dependent subtasks; human time is not silently monetized."]}


def verify_package(args):
    root = root_path(args.root)
    manifest_name = relative(args.manifest)
    m = obj(load_json(safe_file(root, manifest_name)), "manifest")
    schema_one(m, "manifest")
    files = obj(m.get("files"), "manifest.files")
    require(len(files) <= MAX_FILES, "manifest too large")
    require(manifest_name not in files, "manifest cannot hash itself")
    actual = set()
    started = time.monotonic()
    def walk_error(error):
        raise Invalid(f"package discovery failed: {error}")
    for directory, dirs, names in os.walk(root, followlinks=False, onerror=walk_error):
        require(time.monotonic() - started <= 30, "package discovery budget exceeded")
        for dirname in list(dirs):
            p = Path(directory) / dirname
            if p.is_symlink():
                actual.add(p.relative_to(root).as_posix())
                dirs.remove(dirname)
        actual.update((Path(directory)/n).relative_to(root).as_posix() for n in names)
        require(len(actual) <= MAX_FILES + 1, "package file budget exceeded")
    actual.discard(manifest_name)
    issues = []
    total = 0
    for name, expected in files.items():
        hex_digest(expected, "manifest hash")
        try:
            observed = hash_file(root, name, started + 30)
            total += observed["size"]
            require(total <= MAX_TOTAL, "package read budget exceeded")
            if observed["sha256"] != expected:
                issues.append("hash mismatch: " + name)
        except (Invalid, OSError) as exc:
            issues.append(str(exc))
            if total > MAX_TOTAL or time.monotonic() - started > 30:
                break
    extra = sorted(actual - files.keys())
    missing = sorted(files.keys() - actual)
    return {"status": "FAIL" if issues or extra or missing else "PASS", "verified_files": len(files),
            "issues": issues, "extra": extra, "missing": missing,
            "limitations": ["Byte-integrity check, not publisher authentication or proof of behavior."]}


def main(argv=None):
    parser = CLIParser(description=__doc__)
    parser.add_argument("--version", action="version", version=VERSION)
    sub = parser.add_subparsers(dest="operation", required=True)
    p = sub.add_parser("doctor", help="observe root files and Git availability")
    p.add_argument("--root", required=True)
    p.set_defaults(function=doctor)
    p = sub.add_parser("snapshot", help="hash selected local inputs; ignored files need explicit selection")
    p.add_argument("--root", required=True)
    p.add_argument("--project-id", required=True)
    p.add_argument("--path", action="append", default=[])
    p.set_defaults(function=snapshot)
    p = sub.add_parser("scope", help="compare observations to file/directory scopes")
    p.add_argument("--before", required=True)
    p.add_argument("--after", required=True)
    p.add_argument("--allow", action="append", default=[])
    p.add_argument("--protect", action="append", default=[])
    p.set_defaults(function=scope)
    p = sub.add_parser("gate", help="check mechanical agreement; never decide acceptance")
    p.add_argument("--task", required=True)
    p.add_argument("--snapshot", required=True)
    p.add_argument("--evidence", help="omit to prepare current binding before checks")
    p.set_defaults(function=gate)
    p = sub.add_parser("handoff", help="validate and preserve resume facts")
    p.add_argument("--task", required=True)
    p.add_argument("--record", required=True)
    p.set_defaults(function=handoff)
    p = sub.add_parser("api-surface", help="inventory JSON OpenAPI operations; no network or schema validation")
    p.add_argument("--file", required=True)
    p.add_argument("--before")
    p.set_defaults(function=api_surface)
    p = sub.add_parser("eval-summary", help="summarize complete or partial trajectory observations")
    p.add_argument("--file", required=True)
    p.set_defaults(function=eval_summary)
    p = sub.add_parser("verify-package", help="compare complete file tree with SHA-256 manifest")
    p.add_argument("--root", required=True)
    p.add_argument("--manifest", default="MANIFEST.json")
    p.set_defaults(function=verify_package)
    args = None
    try:
        args = parser.parse_args(argv)
        result = args.function(args)
        result = {"tool": "contour-ops", "version": VERSION, "operation": args.operation, **result}
        print(json.dumps(result, ensure_ascii=True, indent=2, allow_nan=False))
        return 2 if result["status"] in {"FAIL", "INCOMPLETE", "REVIEW_REQUIRED"} else 0
    except (Invalid, OSError, ValueError, TypeError, RecursionError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"tool": "contour-ops", "version": VERSION, "operation": args.operation if args else None,
                          "status": "ERROR", "error": str(exc)}, ensure_ascii=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
