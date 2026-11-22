#!/usr/bin/env python3
"""Contour's prepared local workflow entry point. No shell or generated programs."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import uuid

from contour_ops import (VERSION, CLIParser, Invalid, array, contour_directory, control_location,
                         digest, fixed_git, load_json, obj, product_relative, require, storage_path, string)
from contour_state import Controller, StateError
from contour_verify import VerificationError, snapshot_candidate, supervise, validate_check

PROFILE_SCHEMA = 1
PUBLIC_REQUESTS = frozenset({"register", "transition", "revise", "adjust-budget", "update-candidate", "claim",
    "heartbeat", "release", "plan-effect", "reconcile-effect", "resume", "backup"})


def write_json(path: Path, value: dict, *, replace: bool = False) -> None:
    data = (json.dumps(value, sort_keys=True, ensure_ascii=True, indent=2, allow_nan=False) + "\n").encode()
    require(len(data) <= 16 * 1024 * 1024, "Record exceeds 16 MiB")
    path = storage_path(path)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".partial")
    with temporary.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        if replace:
            require(not path.is_symlink(), "Do not replace a symlink record")
            os.replace(temporary, path)
        else:
            os.link(temporary, path)
            temporary.unlink()
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary.exists():
            temporary.unlink()


def toolchain_digest() -> str:
    root = Path(__file__).resolve().parent.parent
    paths = [root / "SKILL.md", *sorted((root / "scripts").glob("contour_*.py")),
             *sorted((root / "references").glob("*.md"))]
    return digest({str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
                   for path in paths})


def validate_profile(profile: dict) -> dict:
    require(isinstance(profile, dict), "Profile must be an object")
    require(type(profile.get("schema_version")) is int and profile["schema_version"] == PROFILE_SCHEMA,
            "Unsupported execution profile schema")
    allowed = {"schema_version", "project_id", "environment_id", "authority_ref", "acceptor",
               "checks", "snapshot_paths", "context7", "reviewed_ref", "host_mode"}
    require(not set(profile) - allowed, "Unknown execution profile fields")
    checked = dict(profile)
    for field in ("project_id", "environment_id", "authority_ref", "acceptor", "reviewed_ref"):
        string(checked.get(field), field)
    require(checked.get("host_mode", "native") == "native", "Only the qualified native-host profile is available")
    checked["host_mode"] = "native"
    require(isinstance(checked.get("checks"), list), "checks must be an array; review-only tasks may use an empty catalog")
    require(len(checked["checks"]) <= 100, "Too many checks")
    checked["checks"] = [validate_check(check) for check in checked["checks"]]
    require(len({check["id"] for check in checked["checks"]}) == len(checked["checks"]), "Duplicate check id")
    selections = {tuple(check["snapshot_paths"]) for check in checked["checks"]}
    require(len(selections) <= 1, "Checks must share one complete input selection for this profile")
    selected = list(next(iter(selections))) if selections else checked.get("snapshot_paths", ["."])
    require(isinstance(selected, list) and 1 <= len(selected) <= 1000, "snapshot_paths must be a nonempty bounded list")
    selected = sorted({product_relative(value, allow_dot=True) for value in selected})
    require(checked.get("snapshot_paths", selected) == selected, "Profile and checks select different inputs")
    checked["snapshot_paths"] = selected
    context = checked.get("context7")
    require(isinstance(context, dict), "A Context7 capability record is required")
    require(context.get("required_for_external_docs") is True, "Context7 remains required for external documentation")
    require(context.get("status") in {"not-needed-yet", "observed", "official-fallback"}, "Unknown Context7 capability status")
    if context["status"] == "observed":
        for field in ("library_id", "requested_version", "served_version", "primary_ref", "observed_at"):
            string(context.get(field), "context7." + field)
    if context["status"] == "official-fallback":
        for field in ("reason", "official_source", "requested_version", "served_version", "observed_at"):
            string(context.get(field), "context7." + field)
    return checked


def initialize(root: Path, workspace: Path | None = None, profile: dict | None = None) -> dict:
    root = storage_path(root)
    require(root.is_dir(), "Project root must be a directory")
    workspace = control_location(root, workspace if workspace is not None else root / ".contour" / "runtime")
    profile = validate_profile(profile)
    fingerprint = toolchain_digest()
    profile_identity = digest({"profile": profile, "toolchain_digest": fingerprint})
    config = {"schema_version": 1, "package_version": VERSION, "root": str(root),
              "profile": profile, "toolchain_digest": fingerprint, "profile_digest": profile_identity}
    local = workspace.is_relative_to(root)
    if local:
        contour_directory(root, create=True)
    require(workspace.parent.is_dir(), "Control workspace parent must exist")
    reused = workspace.exists()
    if reused:
        require(workspace.is_dir(), "Control workspace must be a directory")
    else:
        workspace.mkdir(mode=0o700)
        parent_descriptor = os.open(workspace.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent_descriptor)
        finally:
            os.close(parent_descriptor)
    config_path = workspace / "project.json"
    require(not config_path.is_symlink(), "Project configuration cannot be a symlink")
    if config_path.exists():
        existing = load_json(config_path)
        require(existing == config, "Existing workspace differs; use explicit reconfiguration or preserve it")
    else:
        require(not any(workspace.iterdir()),
                "An uninitialized nonempty workspace needs reconciliation; existing files were preserved")
        write_json(workspace / "project.json", config)
    directories = [workspace / name for name in ("runs", "packets")]
    for path in directories:
        require(not path.is_symlink() and (not path.exists() or path.is_dir()),
                "Runtime path must be a real directory: " + path.name)
    result = Controller(workspace / "state.sqlite3").initialize(profile["project_id"], root, profile_identity)
    for path in directories:
        path.mkdir(mode=0o700, exist_ok=True)
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    parent_descriptor = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(parent_descriptor)
    finally:
        os.close(parent_descriptor)
    return {"status": "OK", "reused": reused, "workspace": str(workspace), "profile_digest": profile_identity,
            "controller": result, "trust": "Cooperative local session; no hostile same-user isolation"}


def open_project(workspace: Path, *, require_current: bool = True):
    workspace = storage_path(workspace)
    require(workspace.is_dir(), "Control workspace must be a directory")
    config = obj(load_json(workspace / "project.json"), "project configuration")
    require(type(config.get("schema_version")) is int and config["schema_version"] == 1, "Unsupported workspace schema")
    profile = validate_profile(config.get("profile"))
    require(digest({"profile": profile, "toolchain_digest": config["toolchain_digest"]}) == config["profile_digest"],
            "Stored profile digest mismatch")
    if require_current:
        require(config["toolchain_digest"] == toolchain_digest(), "Toolchain changed; review and reconfigure before continuing")
    root = storage_path(config["root"])
    require(root.is_dir(), "Project root must be a directory")
    control_location(root, workspace)
    for name in ("runs", "packets"):
        require(storage_path(workspace / name).is_dir(), "Runtime path must be a real directory: " + name)
    controller = Controller(workspace / "state.sqlite3")
    project = controller.status()["project"]
    require(project["root"] == str(root) and project["project_id"] == profile["project_id"],
            "Workspace root/project identity differs from the durable controller")
    if require_current:
        require(project["profile_digest"] == config["profile_digest"],
                "Profile update is incomplete; resume its recorded reconfiguration request")
    return workspace, root, config, controller


def current_candidate(root: Path, config: dict, *, allow_empty: bool = False) -> dict:
    profile = config["profile"]
    report = snapshot_candidate(root, profile["project_id"], profile["snapshot_paths"])
    require(report["status"] == "OBSERVED" and not report["absent_explicit_paths"],
            "Candidate snapshot must be observed and include all selected paths")
    require(allow_empty or report["hashed_count"] > 0,
            "Verification and completion require a nonempty candidate; bootstrap ownership permits an observed empty root")
    return report


def task_root(workspace: Path, root: Path, controller: Controller, task_id: str | None = None,
              explicit: str | None = None) -> Path:
    if explicit is None and task_id is not None:
        explicit = controller.status(task_id)["task"].get("workspace_root", str(root))
    candidate = storage_path(explicit or root)
    require(candidate.is_dir(), "Task workspace must be an existing directory")
    if candidate == root:
        return candidate
    # The host owns Git mutations. Bind an existing native worktree to this
    # project without trusting a directory name or a caller-written receipt.
    def git_path(directory, option):
        value = fixed_git(directory, ["rev-parse", "--path-format=absolute", option])
        return Path(os.fsdecode(value.removesuffix(b"\n"))).resolve()

    for directory in (root, candidate):
        require(git_path(directory, "--show-toplevel") == directory,
                "Assign the actual Git worktree root")
    require(git_path(root, "--git-common-dir") == git_path(candidate, "--git-common-dir"),
            "Task workspace does not belong to this repository")
    records = fixed_git(root, ["worktree", "list", "--porcelain", "-z"]).split(b"\0")
    registered = {Path(os.fsdecode(record[9:])).resolve() for record in records
                  if record.startswith(b"worktree ")}
    require(candidate in registered, "Task workspace is not a registered worktree")
    return candidate


def apply_request(workspace: Path, request: dict) -> dict:
    obj(request, "request")
    workspace, root, config, controller = open_project(workspace)
    require(request.get("op") in PUBLIC_REQUESTS, "Use the prepared check/worker/review operation for execution receipts")
    request = dict(request)
    require("workspace_root" not in request or request["op"] in {"register", "update-candidate"},
            "A workspace change requires an explicit assignment, not a transition override")
    if request["op"] in {"register", "revise"} and "criteria" in request:
        checks = {check["id"]: check for check in config["profile"]["checks"]}
        for criterion in array(request["criteria"], "criteria"):
            obj(criterion, "criterion")
            if criterion.get("kind") != "review":
                for operation in array(criterion.get("operation_ids", []), "criterion.operation_ids"):
                    require(operation in checks, "Criterion operation is absent from the reviewed check catalog")
                    required = {"unittest", "junit"} if criterion.get("kind") == "test" else {"process"}
                    require(checks[operation]["kind"] in required, "Criterion and check kinds differ")
    if request["op"] != "backup":
        root = task_root(workspace, root, controller,
                         None if request["op"] == "register" else request.get("task_id"),
                         request.get("workspace_root"))
    if request["op"] in {"register", "revise", "update-candidate"} or (
            request["op"] == "transition" and request.get("to") in {"ready", "running", "proposed", "accepted"}):
        bootstrap = request["op"] in {"register", "revise", "update-candidate"} or request.get("to") in {"ready", "running"}
        actual = current_candidate(root, config, allow_empty=bootstrap)["digest"]
        if "candidate_digest" in request:
            require(request["candidate_digest"] == actual, "Requested candidate is stale")
        request["candidate_digest"] = actual
        if "profile_digest" in request:
            require(request["profile_digest"] == config["profile_digest"], "Requested profile is stale")
        request["profile_digest"] = config["profile_digest"]
    return controller.apply(request)


def run_check(workspace: Path, request: dict) -> dict:
    obj(request, "check request")
    workspace, root, config, controller = open_project(workspace)
    request = dict(request)
    root = task_root(workspace, root, controller, request.get("task_id"))
    check_id = request.pop("check_id", None)
    checks = {check["id"]: check for check in config["profile"]["checks"]}
    require(check_id in checks, "Check is not in the reviewed profile")
    check = checks[check_id]
    forbidden = {"op", "receipt", "binding", "operation_id", "category", "reserve_seconds", "profile_digest", "candidate_digest",
                 "workspace_destination"}
    require(not set(request) & forbidden, "Execution facts are set by the prepared runner")
    request.update({"op": "begin-attempt", "category": "check", "operation_id": check_id,
                    "reserve_seconds": check["timeout_seconds"] + 5,
                    "profile_digest": config["profile_digest"],
                    "candidate_digest": current_candidate(root, config)["digest"]})
    admitted = controller.apply(request)
    attempt = admitted["attempt"]
    if admitted.get("replayed"):
        return {"status": "RECONCILE", "admission": admitted,
                "reason": "This request was already admitted. Inspect its receipt and process; do not blindly execute it again."}
    run_dir = workspace / "runs" / attempt["attempt_id"]
    receipt = supervise(check, root, run_dir, attempt["binding"])
    task = admitted["task"]
    finished = controller.apply({"op": "finish-attempt", "request_id": uuid.uuid4().hex,
        "task_id": request["task_id"], "expected_revision": task["revision"], "expected_seq": task["seq"],
        "worker_id": request["worker_id"], "generation": request["generation"],
        "attempt_id": attempt["attempt_id"], "receipt": receipt})
    return {"status": receipt["status"], "receipt": receipt, "controller": finished}


def run_plan(workspace: Path, plan: dict) -> dict:
    obj(plan, "plan")
    """Run a finite check list; no loops, shell, code strings or automatic retries."""
    require(isinstance(plan, dict), "Plan must be an object")
    require(set(plan) == {"task_id", "expected_revision", "expected_seq", "worker_id", "generation", "steps"},
            "Unexpected plan fields")
    steps = plan["steps"]
    require(isinstance(steps, list) and 1 <= len(steps) <= 100, "Plan needs 1..100 steps")
    for step in steps:
        require(isinstance(step, dict) and set(step) == {"request_id", "check_id", "criterion_ids"},
                "Each step contains request_id, check_id, criterion_ids")
    require(len({step["request_id"] for step in steps}) == len(steps), "Duplicate plan request identity")
    current = {key: value for key, value in plan.items() if key != "steps"}
    results = []
    for step in steps:
        result = run_check(workspace, {**current, **step})
        results.append(result)
        if result["status"] != "PASS":
            return {"status": "STOPPED", "completed_steps": len(results), "results": results,
                    "remaining_request_ids": [s["request_id"] for s in steps[len(results):]]}
        current["expected_seq"] = result["controller"]["task"]["seq"]
    return {"status": "PASS", "completed_steps": len(results), "results": results,
            "acceptance": "NOT_DECIDED"}


def prepare_host(workspace: Path, request: dict, *, review: bool = False) -> dict:
    """Admit a native-host attempt before the coordinator dispatches a real agent."""
    obj(request, "host request")
    workspace, root, config, controller = open_project(workspace)
    request = dict(request)
    root = task_root(workspace, root, controller, request.get("task_id"))
    assignment = request.pop("assignment", None)
    require(isinstance(assignment, dict), "A native assignment contract is required")
    for field in ("goal", "scope", "output", "stop_conditions"):
        string(assignment.get(field), "assignment." + field)
    require(isinstance(assignment.get("inputs"), list), "assignment.inputs must be an array")
    require(not set(request) & {"op", "category", "binding", "profile_digest", "candidate_digest", "workspace_destination"},
            "Admission facts are prepared by the controller")
    request.update({"op": "begin-attempt", "category": "check" if review else "worker",
                    "profile_digest": config["profile_digest"],
                    "candidate_digest": current_candidate(root, config, allow_empty=not review)["digest"]})
    if not review:
        require(not request.get("criterion_ids"), "A worker result cannot satisfy acceptance criteria")
        request["criterion_ids"] = []
    admission = controller.apply(request)
    attempt = admission["attempt"]
    packet_path = workspace / "packets" / (attempt["attempt_id"] + ".json")
    if admission.get("replayed"):
        return {"status": "RECONCILE", "admission": admission, "packet": str(packet_path),
                "reason": "Reconcile the already admitted host attempt; do not dispatch a second worker."}
    packet = {"schema_version": 1, "kind": "review" if review else "worker",
              "binding": attempt["binding"], "assignment": assignment,
              "project_root": str(root), "profile_digest": config["profile_digest"],
              "skill_entry": str(Path(__file__).resolve().parent.parent / "SKILL.md"),
              "prepared_at": time.time(), "reserve_seconds": request["reserve_seconds"],
              "authority_ref": admission["task"]["authority_ref"], "acceptor": admission["task"]["acceptor"],
              "host_dispatch": "Use the actual available native spawn tool; record its returned id. This file does not launch an agent.",
              "return_contract": "Return one worker-result JSON object with the exact pinned binding in its top-level binding field, actual artifacts/checks, unresolved facts and native host witness. The coordinator passes that unchanged object as the host-result request's result. Never broaden scope or claim acceptance."}
    write_json(packet_path, packet)
    return {"status": "OK", "packet": str(packet_path), "admission": admission,
            "dispatch": "NOT_YET_PERFORMED"}


def finish_host(workspace: Path, request: dict) -> dict:
    obj(request, "host result request")
    workspace, root, config, controller = open_project(workspace)
    root = task_root(workspace, root, controller, request.get("task_id"))
    allowed = {"request_id", "task_id", "expected_revision", "expected_seq", "worker_id", "generation",
               "attempt_id", "witness_ref", "status", "completed", "duration_seconds", "result", "resolution_ref"}
    require(isinstance(request, dict) and set(request) <= allowed, "Unknown host result fields")
    attempt_id = request.get("attempt_id")
    require(isinstance(attempt_id, str) and attempt_id.startswith("attempt-") and
            len(attempt_id) == 40 and all(c in "0123456789abcdef" for c in attempt_id[8:]), "Invalid attempt id")
    packet = load_json(workspace / "packets" / (attempt_id + ".json"))
    string(request.get("witness_ref"), "witness_ref")
    require(request.get("status") in {"PASS", "FAIL", "INCOMPLETE", "ERROR"}, "Invalid host outcome")
    require(type(request.get("completed")) is bool, "completed must be Boolean")
    require(isinstance(request.get("result"), dict), "Return actual result details")
    require(request["result"].get("binding") == packet["binding"],
            "Native result binding differs from the admitted task; do not substitute packet fields for a stale return")
    receipt = {"schema_version": 1, "origin": "HOST_OBSERVED", "kind": packet["kind"],
               "binding": packet["binding"], "candidate_before": packet["binding"]["candidate_digest"],
               "candidate_after": current_candidate(root, config, allow_empty=True)["digest"],
               "status": request["status"], "completed": request["completed"],
               "duration_seconds": request.get("duration_seconds"), "witness_ref": request["witness_ref"],
               "result": request["result"], "origin_limit": "Native host observation supplied by coordinator; not protected authentication"}
    request_data = {key: request[key] for key in ("request_id", "task_id", "expected_revision", "expected_seq",
                   "worker_id", "generation", "attempt_id")}
    if "resolution_ref" in request:
        request_data["resolution_ref"] = request["resolution_ref"]
    result = controller.apply({**request_data, "op": "finish-attempt", "receipt": receipt})
    receipt_path = workspace / "packets" / (attempt_id + ".result." + digest(receipt) + ".json")
    if receipt_path.exists():
        require(load_json(receipt_path) == receipt, "Conflicting existing host receipt")
    else:
        write_json(receipt_path, receipt)
    return result




def _reconfigure_locked(workspace: Path, request: dict) -> dict:
    """Recoverable profile replacement, retaining the database and all spend."""
    workspace, root, existing, controller = open_project(workspace, require_current=False)
    require(isinstance(request, dict) and set(request) == {"request_id", "profile", "reason", "authority_ref"},
            "Reconfiguration needs request_id, profile, reason and authority_ref")
    request_id = request["request_id"]
    require(isinstance(request_id, str) and 1 <= len(request_id) <= 100 and
            all(c.isascii() and (c.isalnum() or c in "-_") for c in request_id), "Invalid reconfiguration request id")
    for field in ("reason", "authority_ref"):
        string(request[field], field)
    profile = validate_profile(request["profile"])
    require(profile["project_id"] == existing["profile"]["project_id"], "Reconfiguration cannot change project identity")
    fingerprint = toolchain_digest()
    new = {"schema_version": 1, "package_version": VERSION, "root": str(root), "profile": profile,
           "toolchain_digest": fingerprint, "profile_digest": digest({"profile": profile, "toolchain_digest": fingerprint})}
    intent_path = workspace / ("reconfigure-" + request_id + ".json")
    if intent_path.exists():
        intent = load_json(intent_path)
        require(intent["request"] == request and intent["to_config"] == new, "Conflicting reconfiguration identity or changed toolchain")
    else:
        require(existing != new, "Profile and toolchain are unchanged")
        intent = {"schema_version": 1, "request": request, "from_config": existing, "to_config": new}
        write_json(intent_path, intent)
    current_profile = controller.status()["project"]["profile_digest"]
    require(current_profile in {intent["from_config"]["profile_digest"], new["profile_digest"]},
            "This reconfiguration was superseded; its old result cannot replace the current profile")
    result = controller.apply({"op": "reconfigure", "request_id": request_id,
        "expected_profile_digest": intent["from_config"]["profile_digest"],
        "new_profile_digest": new["profile_digest"], "reason": request["reason"], "authority_ref": request["authority_ref"]})
    require(controller.status()["project"]["profile_digest"] == new["profile_digest"],
            "Durable profile changed; preserve the current file and reconcile")
    write_json(workspace / "project.json", new, replace=True)
    return {"status": "OK", "controller": result, "profile_digest": new["profile_digest"],
            "next": "Revise affected tasks, retain spend and history, and rerun current evidence before acceptance"}


def reconfigure(workspace: Path, request: dict) -> dict:
    obj(request, "reconfiguration request")
    """Serialize cooperative profile-file and database replacement on Linux."""
    workspace = open_project(workspace, require_current=False)[0]
    lock_path = workspace / "reconfigure.lock"
    descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise Invalid("Another profile update is active; reconcile before retry") from error
        return _reconfigure_locked(workspace, request)
    finally:
        os.close(descriptor)


def compact_result(result: dict, command: str) -> dict:
    """Project check/plan output without changing execution or durable records.

    The versioned envelope retains the full result except for identical nested
    receipt copies. Only paths listed in view.references are reference slots;
    existing $ref-looking data elsewhere is ordinary result data. Expanding each
    listed slot from its target reconstructs the full result. JSON fragments are
    rooted at this envelope. Differing copies remain visible in full.
    """
    require(command in {"check", "plan"}, "Compact output is available only for check and plan")
    # Project the wire data, not Python alias identity shared by possible callers.
    projected = json.loads(json.dumps(result, ensure_ascii=True, allow_nan=False))
    view = {"name": "contour-workflow-compact", "schema_version": 1,
            "references": [], "inconsistencies": []}

    def deduplicate(check_result, path):
        if not isinstance(check_result, dict) or "receipt" not in check_result:
            return
        controller = check_result.get("controller")
        attempt = controller.get("attempt") if isinstance(controller, dict) else None
        if not isinstance(attempt, dict) or "receipt" not in attempt:
            return
        source = path + "/controller/attempt/receipt"
        target = path + "/receipt"
        # Python equality conflates bool/int and int/float; the JSON contract does not.
        first = json.dumps(check_result["receipt"], sort_keys=True, ensure_ascii=True,
                           allow_nan=False, separators=(",", ":"))
        second = json.dumps(attempt["receipt"], sort_keys=True, ensure_ascii=True,
                            allow_nan=False, separators=(",", ":"))
        if first != second:
            view["inconsistencies"].append({"code": "RECEIPT_COPY_MISMATCH", "from": source,
                "to": target, "reason": "Receipt copies differ; both are retained."})
            return
        attempt["receipt"] = {"$ref": target}
        view["references"].append({"from": source, "to": target})

    if command == "check":
        deduplicate(projected, "#/result")
    elif isinstance(projected.get("results"), list):
        for index, check_result in enumerate(projected["results"]):
            deduplicate(check_result, "#/result/results/" + str(index))
    return {"view": view, "result": projected}


def main(argv=None):
    parser = CLIParser(description=__doc__)
    parser.add_argument("--version", action="version", version=VERSION)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Initialize a reviewed profile in root .contour/runtime")
    init.add_argument("--root", required=True)
    init.add_argument("--workspace", help="Control directory; defaults to <root>/.contour/runtime (external paths remain supported)")
    init.add_argument("--profile", required=True)
    for command in ("request", "check", "plan", "worker-packet", "review-packet", "host-result", "reconfigure"):
        sub = commands.add_parser(command)
        sub.add_argument("--workspace", required=True)
        sub.add_argument("--file", required=True)
        if command in {"check", "plan"}:
            sub.add_argument("--view", choices=("full", "compact"), default="full",
                             help="Output only: full result or lossless duplicate-receipt references")
    status = commands.add_parser("status")
    status.add_argument("--workspace", required=True)
    status.add_argument("--task-id")
    args = parser.parse_args(argv)
    if args.command == "init":
        result = initialize(Path(args.root), Path(args.workspace) if args.workspace else None, load_json(args.profile))
    elif args.command == "status":
        result = open_project(Path(args.workspace), require_current=False)[3].status(args.task_id)
    else:
        operation = {"request": apply_request, "check": run_check, "plan": run_plan,
                     "worker-packet": prepare_host, "review-packet": lambda workspace, request: prepare_host(workspace, request, review=True),
                     "host-result": finish_host,
                     "reconfigure": reconfigure}[args.command]
        result = operation(Path(args.workspace), load_json(args.file))
    displayed = compact_result(result, args.command) if getattr(args, "view", "full") == "compact" else result
    print(json.dumps(displayed, sort_keys=True, ensure_ascii=True, allow_nan=False, indent=2))
    return 0 if result.get("status") in {"OK", "PASS"} else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except StateError as error:
        print(json.dumps(error.as_dict(), sort_keys=True, ensure_ascii=True))
        raise SystemExit(1)
    except (Invalid, VerificationError, OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"status": "ERROR", "error": str(error)}, sort_keys=True, ensure_ascii=True))
        raise SystemExit(1)
