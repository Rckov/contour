"""Prepared, non-publishing Git worktree operations for Contour."""
from __future__ import annotations

import json
import os
import re
import selectors
import signal
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

from contour_ops import Invalid, digest, matches, relative, snapshot


def _git(root: Path, args: list[str], *, allow_failure: bool = False) -> dict:
    executable = shutil.which("git")
    if not executable:
        raise Invalid("Git is unavailable")
    if any(key.startswith("GIT_CONFIG") for key in os.environ):
        raise Invalid("Environment-supplied Git configuration needs the project's reviewed adapter")
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    process = subprocess.Popen([executable, "-C", str(root), *args], stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, start_new_session=True)
    captured = {"stdout": bytearray(), "stderr": bytearray()}
    selector = selectors.DefaultSelector()
    deadline, seen = time.monotonic() + 30, 0
    try:
        for name, pipe in (("stdout", process.stdout), ("stderr", process.stderr)):
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ, name)
        while selector.get_map():
            if time.monotonic() > deadline:
                raise Invalid("Git timed out; reconcile any recorded operation before retry")
            for key, _ in selector.select(0.1):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                seen += len(chunk)
                if seen > 2 * 1024 * 1024:
                    raise Invalid("Git output exceeded the supported observation limit")
                captured[key.data].extend(chunk)
        process.wait(timeout=max(0.1, deadline - time.monotonic()))
    finally:
        selector.close()
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=2)
        process.stdout.close()
        process.stderr.close()
    report = {"returncode": process.returncode,
              "stdout": captured["stdout"].decode("utf-8", "strict"),
              "stderr": captured["stderr"].decode("utf-8", "replace")}
    if process.returncode and not allow_failure:
        raise Invalid("Git failed: " + report["stderr"][:4000])
    return report


def repository(root: Path) -> Path:
    root = Path(root).resolve(strict=True)
    actual = Path(_git(root, ["rev-parse", "--show-toplevel"])["stdout"].strip()).resolve()
    if actual != root:
        raise Invalid("Use the actual worktree root")
    return root


def commit_identity(root: Path, revision: str) -> str:
    if not isinstance(revision, str) or not revision or len(revision) > 200 or "\x00" in revision:
        raise Invalid("Invalid Git revision")
    value = _git(root, ["rev-parse", "--verify", "--end-of-options", revision + "^{commit}"])["stdout"].strip()
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value):
        raise Invalid("Git did not return a commit identity")
    return value


def require_cooperative_git(root: Path) -> None:
    """Refuse executable Git configuration the prepared adapter cannot contain."""
    root = repository(root)
    hooks = _git(root, ["config", "--get", "core.hooksPath"], allow_failure=True)
    if hooks["returncode"] == 0:
        raise Invalid("Configured hooks require the project's reviewed integration adapter; no hooks are disabled")
    if hooks["returncode"] != 1:
        raise Invalid("Cannot inspect Git hook configuration")
    monitor = _git(root, ["config", "--get", "core.fsmonitor"], allow_failure=True)
    if monitor["returncode"] not in {0, 1} or (monitor["returncode"] == 0 and monitor["stdout"].strip().lower() not in {"false", "no", "off", "0"}):
        raise Invalid("Active filesystem monitor requires a reviewed project integration adapter")
    filters = _git(root, ["config", "--get-regexp", "^filter\\."], allow_failure=True)
    if filters["returncode"] not in {0, 1}:
        raise Invalid("Cannot inspect Git filter configuration")
    if any(line.split(None, 1)[0].lower().endswith((".clean", ".smudge", ".process")) for line in filters["stdout"].splitlines() if line):
        raise Invalid("Executable checkout filters require the project's reviewed integration adapter")
    drivers = _git(root, ["config", "--get-regexp", "^merge\\."], allow_failure=True)
    if drivers["returncode"] not in {0, 1}:
        raise Invalid("Cannot inspect Git merge-driver configuration")
    if any(line.split(None, 1)[0].lower().endswith(".driver") for line in drivers["stdout"].splitlines() if line):
        raise Invalid("Executable merge drivers require the project's reviewed integration adapter")
    directory = Path(_git(root, ["rev-parse", "--path-format=absolute", "--git-path", "hooks"])["stdout"].strip())
    if directory.exists():
        for hook in directory.iterdir():
            if not hook.name.endswith(".sample") and (hook.is_symlink() or (hook.is_file() and os.access(hook, os.X_OK))):
                raise Invalid("Active Git hooks require the project's reviewed integration adapter; no hooks are bypassed")


def _source_guard(root: Path) -> dict:
    observed = snapshot(SimpleNamespace(root=str(root), project_id="git-source-guard", path=["."]))
    if observed["status"] != "OBSERVED":
        raise Invalid("Cannot completely observe source files before a worktree operation")
    index = _git(root, ["ls-files", "--stage", "-z"])["stdout"]
    return {"source_digest": observed["digest"], "index_digest": digest(index)}


def _new_location(root: Path, value: Path) -> Path:
    value = Path(value).absolute()
    if value.exists() or value.is_symlink():
        raise Invalid("The destination must not already exist")
    parent = value.parent.resolve(strict=True)
    value = parent / value.name
    if value == root or root in value.parents:
        raise Invalid("Worktrees and operation records must be outside the source tree")
    return value


def _write_record(directory: Path, name: str, value: dict) -> None:
    path = directory / name
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, sort_keys=True, indent=2, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def create_worktree(root: Path, destination: Path, base: str, record_dir: Path) -> dict:
    """Persist intent, then create a separate detached worktree; never clean source."""
    root = repository(root)
    require_cooperative_git(root)
    destination = _new_location(root, destination)
    record_dir = _new_location(root, record_dir)
    if destination == record_dir or destination in record_dir.parents or record_dir in destination.parents:
        raise Invalid("Records and worktree must use disjoint directories")
    base_commit = commit_identity(root, base)
    source_guard = _source_guard(root)
    source_status = _git(root, ["status", "--porcelain=v1", "-z", "--untracked-files=all"])["stdout"]
    record_dir.mkdir(mode=0o700)
    parent_descriptor = os.open(record_dir.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent_descriptor)
    finally:
        os.close(parent_descriptor)
    intent = {"schema_version": 1, "operation_id": uuid.uuid4().hex,
              "kind": "git-worktree", "root": str(root), "destination": str(destination),
              "base_commit": base_commit, "started_at": time.time(),
              "source_status_digest": digest(source_status),
              "source_guard": source_guard,
              "reconcile_next": "Inspect git worktree list and destination before retry; never force-remove it."}
    _write_record(record_dir, "intent.json", intent)
    result = _git(root, ["worktree", "add", "--detach", str(destination), base_commit], allow_failure=True)
    current_status = _git(root, ["status", "--porcelain=v1", "-z", "--untracked-files=all"])["stdout"]
    after_guard = _source_guard(root)
    report = {**intent, "status": "CREATED" if result["returncode"] == 0 else "INCOMPLETE",
              "result": result, "source_status_unchanged": current_status == source_status,
              "source_bytes_and_index_unchanged": after_guard == source_guard,
              "ownership": "Separate worktree; no access-control or writer-lock claim"}
    if not report["source_status_unchanged"] or not report["source_bytes_and_index_unchanged"]:
        report["status"] = "INCOMPLETE"
    _write_record(record_dir, "result.json", report)
    return report


def integrate(root: Path, destination: Path, base: str, commits: list[str],
              allowed_paths: list[str], record_dir: Path) -> dict:
    """Apply identified commits in a new worktree, leaving a candidate for checks."""
    root = repository(root)
    require_cooperative_git(root)
    if not isinstance(commits, list) or not 1 <= len(commits) <= 100:
        raise Invalid("Integrate requires 1..100 ordered commits")
    if not isinstance(allowed_paths, list) or not allowed_paths:
        raise Invalid("An explicit integration write scope is required")
    for rule in allowed_paths:
        relative(rule.rstrip("/"), allow_dot=True)
    pinned_base = commit_identity(root, base)
    pinned = [commit_identity(root, item) for item in commits]
    if len(set(pinned)) != len(pinned):
        raise Invalid("Duplicate integration commit")
    changed = set()
    for commit in pinned:
        parent_line = _git(root, ["rev-list", "--parents", "-n", "1", commit])["stdout"].split()
        if len(parent_line) != 2:
            raise Invalid("Only ordinary single-parent commits are supported; review merges explicitly")
        names = _git(root, ["diff-tree", "--no-commit-id", "--name-only", "--no-renames", "-r", "-z", commit])["stdout"].split("\x00")
        changed.update(name for name in names if name)
    outside = sorted(name for name in changed if not any(matches(name, rule) for rule in allowed_paths))
    if outside:
        raise Invalid("Integration changes paths outside admitted scope: " + repr(outside[:20]))
    creation = create_worktree(root, destination, pinned_base, record_dir)
    if creation["status"] != "CREATED":
        return creation
    destination = Path(creation["destination"])
    record_dir = Path(record_dir).resolve()
    intent = {"schema_version": 1, "kind": "git-integration", "base_commit": pinned_base,
              "commits": pinned, "allowed_paths": allowed_paths, "incoming_changed_paths": sorted(changed),
              "reconcile_next": "Inspect this worktree and CHERRY_PICK_HEAD/sequencer; preserve conflicts and rerun checks after repair."}
    _write_record(record_dir, "integration-intent.json", intent)
    result = _git(destination, ["cherry-pick", "--no-commit", *pinned], allow_failure=True)
    # A cherry-pick can follow renames in the target base. Observe both the index
    # and working tree against that pinned base, retaining both sides of renames.
    effective = set()
    for flags in ([], ["--cached"]):
        names = _git(destination, ["diff", *flags, "--no-ext-diff", "--no-textconv",
                     "--name-only", "--no-renames", "-z", pinned_base, "--"])["stdout"].split("\x00")
        effective.update(name for name in names if name)
    untracked = _git(destination, ["ls-files", "--others", "-z"])["stdout"].split("\x00")
    effective.update(name for name in untracked if name)
    outside = sorted(name for name in effective if not any(matches(name, rule) for rule in allowed_paths))
    source_preserved = _source_guard(root) == creation["source_guard"]
    report = {**intent, "status": "CANDIDATE" if result["returncode"] == 0 else "CONFLICT",
              "worktree": str(destination), "result": result,
              "changed_paths": sorted(effective), "outside_scope_paths": outside,
              "scope_status": "OUTSIDE_SCOPE" if outside else "WITHIN_SCOPE",
              "source_bytes_and_index_unchanged": source_preserved,
              "verification": "REQUIRED_ON_INTEGRATED_CANDIDATE", "acceptance": "NOT_DECIDED",
              "publication": "NOT_PERFORMED"}
    if outside or not source_preserved:
        report["status"] = "INCOMPLETE"
    _write_record(record_dir, "integration-result.json", report)
    return report
