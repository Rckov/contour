#!/usr/bin/env python3
"""Prepared local verification. Process observation is not hostile-agent isolation."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
from types import SimpleNamespace
import uuid
import xml.etree.ElementTree as ET

import contour_ops as ops

SCHEMA_VERSION = 1
RUNNER_VERSION = "1.0"
CHECK_KINDS = frozenset({"unittest", "junit", "process"})
JUNIT_DIALECTS = frozenset({"junit-leaf-v1"})
RECEIPT_STATUSES = frozenset({"PASS", "FAIL", "INCOMPLETE", "ERROR"})
TEST_FIELDS = frozenset({"selected", "succeeded", "failed", "errors", "skipped",
                         "expected_failures", "unexpected_successes"})
MAX_RESULT_BYTES = 1024 * 1024
MAX_OUTPUT_BYTES = 16 * 1024 * 1024
RESERVED_ENV = frozenset({"PATH", "HOME", "TMPDIR", "TMP", "TEMP", "PYTHONPATH",
                          "PYTHONHOME", "LD_PRELOAD", "LD_LIBRARY_PATH", "BASH_ENV", "ENV"})


class VerificationError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise VerificationError(message)


def _text(value, name):
    require(isinstance(value, str) and bool(value.strip()) and "\0" not in value,
            f"{name} must be a nonempty string without NUL")
    require(len(value) <= 8192, f"{name} exceeds the string budget")
    return value


def _number(value, name, lower, upper):
    require(type(value) in (int, float) and lower <= value <= upper and math.isfinite(value),
            f"{name} must be finite and between {lower} and {upper}")
    return value


def validate_check(check):
    """Return a canonical catalog entry; there are no per-run command overrides."""
    require(isinstance(check, dict), "check must be an object")
    allowed = {"id", "kind", "cwd", "timeout_seconds", "output_limit_bytes", "snapshot_paths", "env"}
    kind = check.get("kind")
    require(kind in CHECK_KINDS, "unknown check kind")
    allowed |= ({"discover", "pattern", "minimum_passed"} if kind == "unittest" else
                {"argv", "dialect", "producer", "minimum_passed"} if kind == "junit" else {"argv", "purpose"})
    require(not set(check) - allowed, "unknown check fields: " + ", ".join(sorted(set(check) - allowed)))
    checked = {
        "id": _text(check.get("id"), "check.id"), "kind": kind,
        "cwd": ops.relative(check.get("cwd", "."), allow_dot=True),
        "timeout_seconds": _number(check.get("timeout_seconds", 120), "timeout_seconds", 0.05, 3600),
        "output_limit_bytes": check.get("output_limit_bytes", MAX_RESULT_BYTES),
    }
    require(type(checked["output_limit_bytes"]) is int and 1024 <= checked["output_limit_bytes"] <= MAX_OUTPUT_BYTES,
            "output_limit_bytes must be an integer from 1024 to 16777216")
    paths = check.get("snapshot_paths", ["."])
    require(isinstance(paths, list) and paths and len(paths) <= 1000, "snapshot_paths must be a nonempty bounded list")
    checked["snapshot_paths"] = sorted({ops.relative(p, allow_dot=True) for p in paths})
    env = check.get("env", {})
    require(isinstance(env, dict) and len(env) <= 64, "env must be a bounded string map")
    for key, value in env.items():
        require(isinstance(key, str) and re.fullmatch(r"[A-Z_][A-Z0-9_]*", key), "invalid environment key")
        require(key not in RESERVED_ENV, f"reserved environment key: {key}")
        require(isinstance(value, str) and "\0" not in value and len(value) <= 8192, "invalid environment value")
    checked["env"] = dict(sorted(env.items()))
    if kind == "unittest":
        checked["discover"] = ops.relative(check.get("discover", "tests"), allow_dot=True)
        pattern = _text(check.get("pattern", "test*.py"), "pattern")
        require("/" not in pattern and "\\" not in pattern and len(pattern) <= 128, "pattern must be a file-name pattern")
        checked["pattern"] = pattern
    else:
        argv = check.get("argv")
        require(isinstance(argv, list) and 1 <= len(argv) <= 128, "argv must be a bounded argument array")
        checked["argv"] = [_text(value, "argv entry") for value in argv]
        require(Path(argv[0]).is_absolute(), "argv executable must be an absolute reviewed path")
        executable_name = Path(argv[0]).name.lower()
        require(executable_name not in {"sh", "bash", "dash", "zsh", "ksh", "fish", "cmd", "cmd.exe", "pwsh", "powershell"},
                "direct shell commands are not a prepared process check")
        if executable_name.startswith(("python", "node", "perl", "ruby")):
            require(not any(arg.startswith(("-c", "-e", "--eval", "--print")) for arg in argv[1:]),
                    "inline interpreter programs are not prepared operations")
        if executable_name.startswith("python"):
            require(not any(re.match(r"^-[BEdhiIOqRsSuvVx]*c", arg) for arg in argv[1:]),
                    "combined inline interpreter flags are not prepared operations")
        if executable_name.startswith(("node", "perl", "ruby")):
            require(not any(re.match(r"^-[A-Za-z]*[ep]", arg) for arg in argv[1:]),
                    "combined inline interpreter flags are not prepared operations")
        if kind == "junit":
            require(argv.count("{report}") == 1 and not any("{report}" in arg and arg != "{report}" for arg in argv),
                    "junit argv needs exactly one standalone {report} placeholder")
            require(check.get("dialect") in JUNIT_DIALECTS, "unsupported JUnit dialect")
            checked["dialect"] = check["dialect"]
            checked["producer"] = _text(check.get("producer"), "producer")
        else:
            require(check.get("purpose") in {"build", "lint"}, "process purpose must be build or lint")
            checked["purpose"] = check["purpose"]
    if kind in {"unittest", "junit"}:
        minimum = check.get("minimum_passed", 1)
        require(type(minimum) is int and 1 <= minimum <= 1000000, "minimum_passed must be a positive bounded integer")
        checked["minimum_passed"] = minimum
    return checked


def snapshot_candidate(project_root, project_id, selection_paths=None):
    """Use exactly the same snapshot contract for admission and verification."""
    return ops.snapshot(SimpleNamespace(root=str(project_root), project_id=project_id,
                                        path=["."] if selection_paths is None else selection_paths))


def _now():
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path, value):
    data = (json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False, indent=2) + "\n").encode()
    require(len(data) <= ops.MAX_JSON, "record exceeds JSON budget")
    temp = path.with_name(path.name + ".partial")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temp.exists():
            temp.unlink()


def _directory(root, relative):
    current = root
    for part in Path(relative).parts:
        current /= part
        require(not current.is_symlink(), "symlink directory is not followed")
    require(current.is_dir(), "working or discovery directory does not exist")
    require(current.resolve().is_relative_to(root), "directory escapes project root")
    return current


def _group_state(pgid):
    """Observe Linux group members, excluding zombies that cannot execute effects."""
    members, unknown = [], False
    try:
        with os.scandir("/proc") as entries:
            for entry in entries:
                if not entry.name.isdecimal():
                    continue
                try:
                    data = (Path(entry.path) / "stat").read_text()
                    fields = data[data.rfind(")") + 2:].split()
                    if int(fields[2]) == pgid:
                        members.append({"pid": int(entry.name), "state": fields[0]})
                except FileNotFoundError:
                    continue
                except (OSError, ValueError, IndexError):
                    unknown = True
    except OSError:
        unknown = True
    live = [member for member in members if member["state"] not in {"Z", "X"}]
    return {"members": members, "live": live, "complete": not unknown}


def _signal_group(pgid, sig):
    try:
        os.killpg(pgid, sig)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _supervised_process(argv, cwd, env, directory, timeout, output_limit):
    started = time.monotonic()
    process = None
    selector = selectors.DefaultSelector()
    streams = {}
    retained = 0
    stop_reason = None
    termination_at = None
    drain_deadline = None
    group_after = {"members": [], "live": [], "complete": False}
    try:
        for name in ("stdout", "stderr"):
            path = directory / (name + ".log")
            streams[name] = {"stream": path.open("xb"), "path": str(path), "bytes_seen": 0,
                             "bytes_retained": 0, "digest": hashlib.sha256()}
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   shell=False, start_new_session=True, close_fds=True, bufsize=0)
        _atomic_json(directory / "launch.json", {"pid": process.pid, "pgid": process.pid,
                                                "observed_at": _now(), "effect_status": "sent"})
        for name, pipe in (("stdout", process.stdout), ("stderr", process.stderr)):
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ, name)
        deadline = started + timeout
        while True:
            current = time.monotonic()
            returncode = process.poll()
            if stop_reason is None and current >= deadline and (returncode is None or selector.get_map()):
                stop_reason = "timeout"
            if stop_reason is not None and termination_at is None:
                _signal_group(process.pid, signal.SIGTERM)
                termination_at = current
                drain_deadline = current + 1.5
            if termination_at is not None and current - termination_at >= 0.3:
                _signal_group(process.pid, signal.SIGKILL)
            if drain_deadline is not None and current >= drain_deadline:
                break
            if returncode is not None and not selector.get_map():
                group_after = _group_state(process.pid)
                if group_after["live"] and stop_reason is None:
                    stop_reason = "lingering_processes"
                    continue
                if not group_after["live"]:
                    break
            events = selector.select(timeout=0.05)
            for key, _ in events:
                try:
                    chunk = os.read(key.fileobj.fileno(), 65536)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                target = streams[key.data]
                target["bytes_seen"] += len(chunk)
                keep = chunk[:max(0, output_limit - retained)]
                target["stream"].write(keep)
                target["digest"].update(keep)
                target["bytes_retained"] += len(keep)
                retained += len(keep)
                if len(keep) != len(chunk) and stop_reason is None:
                    stop_reason = "output_limit"
        if process.poll() is None:
            _signal_group(process.pid, signal.SIGKILL)
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            stop_reason = stop_reason or "unreaped_child"
        group_after = _group_state(process.pid)
        return {"launched": True, "pid": process.pid, "returncode": process.returncode,
                "stop_reason": stop_reason, "duration_seconds": time.monotonic() - started,
                "process_completed": process.returncode is not None,
                "process_group": group_after,
                "cleanup_verified": group_after["complete"] and not group_after["live"]}
    finally:
        if process is not None:
            _signal_group(process.pid, signal.SIGKILL)
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                pass
        for key in list(selector.get_map().values()):
            selector.unregister(key.fileobj)
            key.fileobj.close()
        selector.close()
        for entry in streams.values():
            entry["stream"].flush()
            os.fsync(entry["stream"].fileno())
            entry["stream"].close()
        # Store independently of the receipt so an interrupted final write is visible.
        logs = [{key: value for key, value in entry.items() if key not in {"stream", "digest"}}
                | {"sha256": entry["digest"].hexdigest(), "bytes": entry["bytes_retained"]}
                for entry in streams.values()]
        _atomic_json(directory / "logs.json", logs)


def _worker_report(path, run_id):
    require(path.is_file() and not path.is_symlink(), "worker report is missing or not regular")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "worker report is not regular")
        data = stream.read(MAX_RESULT_BYTES + 1)
    require(len(data) <= MAX_RESULT_BYTES, "worker report exceeds size budget")
    value = json.loads(data, object_pairs_hook=ops.pairs_unique, parse_constant=ops.no_constant)
    require(isinstance(value, dict), "worker report must be an object")
    require(type(value.get("schema_version")) is int and value["schema_version"] == SCHEMA_VERSION,
            "worker schema mismatch")
    require(value.get("protocol") == "contour-unittest-v1" and value.get("run_id") == run_id,
            "worker protocol or run identity mismatch")
    require(type(value.get("completed")) is bool and type(value.get("successful")) is bool,
            "worker completion flags must be Boolean")
    tests = value.get("tests")
    require(isinstance(tests, dict) and set(tests) == TEST_FIELDS, "worker test count fields mismatch")
    for name, count in tests.items():
        require(type(count) is int and 0 <= count <= 1000000, f"invalid worker count: {name}")
    for name in ("executed", "discovery_errors", "fixture_skips"):
        require(type(value.get(name)) is int and 0 <= value[name] <= 1000000, f"invalid worker count: {name}")
    require(tests["succeeded"] <= value["executed"] <= tests["selected"], "inconsistent worker execution counts")
    return value, hashlib.sha256(data).hexdigest()


def parse_junit(path):
    """Read the deliberately narrow, bounded junit-leaf-v1 interchange dialect."""
    require(path.is_file() and not path.is_symlink(), "JUnit report is missing or not regular")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "JUnit report is not regular")
        data = stream.read(MAX_RESULT_BYTES + 1)
    require(len(data) <= MAX_RESULT_BYTES, "JUnit report exceeds size budget")
    text = data.decode("utf-8-sig", errors="strict")
    require("\0" not in text, "JUnit report must be UTF-8")
    require(not re.search(r"<!\s*(DOCTYPE|ENTITY)", text, re.IGNORECASE), "DTD and entity declarations are forbidden")
    declaration = re.match(r"\s*<\?xml\b.*?\?>", text, re.DOTALL)
    if declaration:
        encoding = re.search(r"encoding\s*=\s*['\"]([^'\"]+)['\"]", declaration.group(), re.IGNORECASE)
        require(encoding is None or encoding[1].lower() in {"utf-8", "utf8"}, "unsupported XML encoding")
    tree = ET.fromstring(text)
    nodes = list(tree.iter())
    require(len(nodes) <= 50000, "JUnit node budget exceeded")
    require(tree.tag in {"testsuite", "testsuites"}, "unsupported JUnit root")
    suites = [tree] if tree.tag == "testsuite" else list(tree)
    require(all(suite.tag == "testsuite" for suite in suites), "testsuites must contain only leaf testsuite elements")
    require(len(suites) <= 1000, "JUnit suite budget exceeded")
    counts = {key: 0 for key in TEST_FIELDS}
    identities = set()

    def auxiliary(element):
        require(element.tag in {"system-out", "system-err", "properties"}, "unsupported JUnit metadata")
        if element.tag == "properties":
            for child in element:
                require(child.tag == "property" and not list(child), "unsupported JUnit property")
        else:
            require(not list(element), "nested log markup is unsupported")

    def counters(element, expected, required):
        for key in ("tests", "failures", "errors", "skipped"):
            value = element.get(key)
            if value is None and not required:
                continue
            require(isinstance(value, str) and re.fullmatch(r"[0-9]+", value) and len(value) <= 9,
                    f"JUnit {key} counter must be a bounded nonnegative integer")
            require(int(value) == expected[key], f"JUnit {key} counter disagrees with test cases")

    aggregate = {key: 0 for key in ("tests", "failures", "errors", "skipped")}
    for suite in suites:
        local = {key: 0 for key in aggregate}
        for case in suite:
            if case.tag != "testcase":
                auxiliary(case)
                continue
            name = _text(case.get("name"), "JUnit testcase.name")
            classname = case.get("classname", "")
            require(len(classname) <= 8192, "JUnit classname exceeds budget")
            identity = (classname, name)
            require(identity not in identities, "duplicate JUnit testcase identity")
            identities.add(identity)
            require(len(identities) <= 10000, "JUnit testcase budget exceeded")
            require(case.get("status") in {None, "run", "passed"}, "unsupported JUnit testcase status")
            outcomes = []
            for child in case:
                if child.tag in {"failure", "error", "skipped"}:
                    require(not list(child), "nested outcome markup is unsupported")
                    outcomes.append(child)
                else:
                    auxiliary(child)
            require(len(outcomes) <= 1, "multiple testcase outcomes are unsupported")
            local["tests"] += 1
            counts["selected"] += 1
            if not outcomes:
                counts["succeeded"] += 1
            elif outcomes[0].tag == "failure":
                counts["failed"] += 1
                local["failures"] += 1
            elif outcomes[0].tag == "error":
                counts["errors"] += 1
                local["errors"] += 1
            else:
                field = "expected_failures" if outcomes[0].get("type") == "pytest.xfail" else "skipped"
                counts[field] += 1
                local["skipped"] += 1
        counters(suite, local, required=True)
        for key in aggregate:
            aggregate[key] += local[key]
    if tree.tag == "testsuites":
        counters(tree, aggregate, required=False)
    return {"tests": counts, "dialect": "junit-leaf-v1", "suites": len(suites),
            "report_digest": hashlib.sha256(data).hexdigest()}


def supervise(check, project_root, run_dir, binding):
    """Capture an authorized catalog check. No result automatically grants acceptance."""
    receipt = {"schema_version": SCHEMA_VERSION, "runner_version": RUNNER_VERSION,
               "origin": "LOCAL_OBSERVED", "run_id": uuid.uuid4().hex,
               "kind": check.get("kind") if isinstance(check, dict) else None,
               "binding": binding, "status": "ERROR", "completed": False,
               "candidate_before": None, "candidate_after": None, "tests": None,
               "argv": [], "cwd": None, "returncode": None, "logs": [],
               "duration_seconds": 0.0, "timeout_seconds": None, "started_at": _now(),
               "launch_attempted": False,
               "effect_status": "planned", "reconcile_next": "No process was launched.",
               "acceptance": "NOT_DECIDED", "errors": [],
               "trust_limits": ["Same-user processes can alter reports, controls, and the ledger.",
                                "Hashes check bytes; they do not authenticate the writer.",
                                "Process-group supervision is not a sandbox."]}
    directory = None
    launched = False
    begin = time.monotonic()
    try:
        require(platform.system() == "Linux", "this runner profile is qualified only for Linux")
        checked = validate_check(check)
        require(isinstance(binding, dict), "binding must be an object")
        binding = json.loads(json.dumps(binding, sort_keys=True, allow_nan=False))
        receipt["binding"] = binding
        project_id = _text(binding.get("project_id"), "binding.project_id")
        ops.hex_digest(binding.get("candidate_digest"), "binding.candidate_digest")
        ops.hex_digest(binding.get("profile_digest"), "binding.profile_digest")
        criteria = binding.get("criterion_ids")
        require(isinstance(criteria, list) and criteria and len(criteria) == len(set(criteria)),
                "binding.criterion_ids must be unique nonempty strings")
        for criterion in criteria:
            _text(criterion, "criterion id")
        root = Path(project_root).resolve()
        require(root.is_dir(), "project root must be a directory")
        target = Path(run_dir).absolute()
        require(not target.exists() and not target.is_symlink(), "run directory must not already exist")
        parent = target.parent.resolve(strict=True)
        require(not (parent / target.name).is_relative_to(root), "run directory must be outside the project root")
        require(not root.is_relative_to(parent / target.name), "project root cannot be inside the run directory")
        new_directory = parent / target.name
        new_directory.mkdir(mode=0o700)
        directory = new_directory
        parent_descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent_descriptor)
        finally:
            os.close(parent_descriptor)
        cwd = _directory(root, checked["cwd"])
        before = ops.checked_snapshot(snapshot_candidate(root, project_id, checked["snapshot_paths"]))
        receipt["candidate_before"] = before["digest"]
        require(before["digest"] == binding["candidate_digest"], "candidate does not match the admitted binding")
        require(before["hashed_count"] > 0 and not before["absent_explicit_paths"], "candidate selection is empty or missing inputs")
        _atomic_json(directory / "candidate-before.json", before)
        (directory / "tmp").mkdir(mode=0o700)
        report_path = directory / ("junit-report.xml" if checked["kind"] == "junit" else "unittest-report.json")
        if checked["kind"] == "unittest":
            discover = _directory(root, checked["discover"])
            discovery_files = set(ops.selected_files(root, [checked["discover"]]))
            require(discovery_files <= set(before["files"]), "test discovery inputs are outside the candidate selection")
            executable = str(Path(sys.executable).resolve())
            argv = [executable, "-I", "-B", str(Path(__file__).with_name("contour_unittest.py").resolve()),
                    "--root", str(root), "--discover", str(discover), "--pattern", checked["pattern"],
                    "--run-id", receipt["run_id"], "--report", str(report_path)]
        else:
            argv = [str(report_path) if item == "{report}" else item for item in checked["argv"]]
            executable = str(Path(argv[0]).resolve(strict=True))
            require(Path(executable).is_file() and os.access(executable, os.X_OK), "reviewed executable is not executable")
            argv[0] = executable
            for argument in argv[1:]:
                possible_input = Path(argument) if Path(argument).is_absolute() else cwd / argument
                if possible_input.is_file() and possible_input.resolve().is_relative_to(root):
                    require(possible_input.resolve().relative_to(root).as_posix() in before["files"],
                            "command file input is outside the candidate selection")
        env = {"PATH": str(Path(executable).parent) + os.pathsep + "/usr/bin:/bin",
               "TMPDIR": str(directory / "tmp"),
               "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
               **checked["env"]}
        receipt.update({"check_id": checked["id"], "check_digest": ops.digest(checked),
                        "profile_digest": binding["profile_digest"], "criteria_digest": ops.digest(criteria),
                        "command_digest": ops.digest(argv), "argv": argv, "cwd": str(cwd),
                        "timeout_seconds": checked["timeout_seconds"], "snapshot_paths": checked["snapshot_paths"],
                        "environment": {"python": sys.version.split()[0], "system": platform.system(),
                                        "machine": platform.machine(), "uid": os.getuid(),
                                        "explicit_keys": sorted(checked["env"]), "digest": ops.digest(env)},
                        "purpose": checked.get("purpose", "test")})
        controls = {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                    for name in ("contour_verify.py", "contour_unittest.py", "contour_ops.py")}
        receipt["runner_controls_digest"] = ops.digest(controls)
        _atomic_json(directory / "intent.json", {**receipt, "reconcile_next":
                     "Before retry, inspect launch.json, receipt.json, process identity, and effects; launch may be unknown."})
        receipt["launch_attempted"] = True
        observation = _supervised_process(argv, cwd, env, directory, checked["timeout_seconds"], checked["output_limit_bytes"])
        launched = observation["launched"]
        receipt.update(observation)
        receipt["logs"] = ops.load_json(directory / "logs.json")
        receipt["effect_status"] = "succeeded" if observation["process_completed"] else "unknown"
        receipt["completed"] = observation["process_completed"] and observation["cleanup_verified"] and not observation["stop_reason"]
        after = ops.checked_snapshot(snapshot_candidate(root, project_id, checked["snapshot_paths"]))
        receipt["candidate_after"] = after["digest"]
        _atomic_json(directory / "candidate-after.json", after)
        current_controls = {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() for name in controls}
        stable = before["digest"] == after["digest"] and controls == current_controls
        if not stable:
            receipt["errors"].append("candidate or runner controls changed during verification")
        if checked["kind"] == "unittest" and receipt["completed"]:
            worker, report_digest = _worker_report(report_path, receipt["run_id"])
            receipt["tests"] = worker["tests"]
            receipt["test_details"] = {key: worker[key] for key in ("executed", "discovery_errors", "subtests", "fixture_skips")}
            receipt["report"] = {"path": str(report_path), "sha256": report_digest}
            receipt["completed"] = receipt["completed"] and worker["completed"]
            tests = worker["tests"]
            failed = tests["failed"] + tests["errors"] + tests["unexpected_successes"] + worker["discovery_errors"]
            meaningful = tests["succeeded"] >= checked["minimum_passed"] and tests["selected"] > 0
            complete_selection = worker["executed"] == tests["selected"]
            if not meaningful:
                receipt["errors"].append("no sufficient successful test cases were observed")
            passed = bool(worker["successful"] and not failed and meaningful and complete_selection)
            receipt["status"] = "PASS" if passed and receipt["returncode"] == 0 else ("FAIL" if failed or receipt["returncode"] else "INCOMPLETE")
        elif checked["kind"] == "junit" and receipt["completed"]:
            junit = parse_junit(report_path)
            receipt["tests"] = junit["tests"]
            receipt["test_details"] = {"dialect": junit["dialect"], "producer": checked["producer"], "suites": junit["suites"]}
            receipt["report"] = {"path": str(report_path), "sha256": junit["report_digest"]}
            tests = junit["tests"]
            failed = tests["failed"] + tests["errors"] + tests["unexpected_successes"]
            meaningful = tests["succeeded"] >= checked["minimum_passed"] and tests["selected"] > 0
            if not meaningful:
                receipt["errors"].append("no sufficient successful test cases were observed")
            receipt["status"] = "PASS" if not failed and meaningful and receipt["returncode"] == 0 else ("FAIL" if failed or receipt["returncode"] else "INCOMPLETE")
        else:
            receipt["status"] = "PASS" if receipt["returncode"] == 0 else "FAIL"
        if not receipt["completed"] or not stable:
            receipt["status"] = "INCOMPLETE"
        if receipt["status"] == "PASS":
            receipt["reconcile_next"] = "Verify criterion sufficiency and record independent acceptance; no process retry is needed."
        elif observation["stop_reason"] or not observation["cleanup_verified"]:
            receipt["effect_status"] = "unknown" if not observation["cleanup_verified"] else "partial"
            receipt["reconcile_next"] = "Inspect partial outcomes and process/effect state before any authorized retry."
        else:
            receipt["effect_status"] = "failed" if receipt["returncode"] else "succeeded"
            receipt["reconcile_next"] = "Retain this attempt; diagnose the result and reverify any corrected candidate."
    except (VerificationError, ops.Invalid, OSError, ValueError, TypeError, KeyError, ET.ParseError, KeyboardInterrupt) as exc:
        receipt["errors"].append(str(exc) or "verification interrupted")
        receipt["status"] = "INCOMPLETE" if isinstance(exc, KeyboardInterrupt) else "ERROR"
        receipt["completed"] = False
        if directory is not None and (directory / "launch.json").exists():
            launched = True
        if launched or receipt["launch_attempted"]:
            receipt["effect_status"] = "unknown"
            receipt["reconcile_next"] = "Inspect recorded launch and actual effects before retry; the final observation is incomplete."
    finally:
        receipt["finished_at"] = _now()
        receipt["duration_seconds"] = time.monotonic() - begin
        if directory is not None:
            try:
                _atomic_json(directory / "receipt.json", receipt)
            except (OSError, ValueError) as exc:
                receipt["status"] = "ERROR"
                receipt["completed"] = False
                receipt["errors"].append("receipt persistence failed: " + str(exc))
    return receipt
