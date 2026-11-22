#!/usr/bin/env python3
"""Bounded, read-only project context. Qualified runtime: Linux, Python 3.12.

Source files are opened relative to directory descriptors without following
symlinks. Git is an optional, fixed-argument inventory helper. Nothing imports
project code, interprets shell/regex input, executes tests, or contacts a service.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import threading
import time
import tokenize
import tomllib

SCHEMA_VERSION = 1
VERSION = "1.0"
MAX_ENTRIES = 20000
MAX_PATHS = 128
MAX_DEPTH = 64
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_READ_BYTES = 128 * 1024 * 1024
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
MAX_GIT_BYTES = 4 * 1024 * 1024
MAX_ERRORS = 32
DEFAULT_EXCLUDES = (".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv")
MANIFESTS = {
    "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock",
    "pyproject.toml", "uv.lock", "poetry.lock", "requirements.txt",
    "Cargo.toml", "Cargo.lock", "go.mod", "go.sum", "pom.xml",
    "build.gradle", "build.gradle.kts", "Makefile", "justfile", "CMakeLists.txt",
}


class ContextError(ValueError):
    """Invalid request or an explicitly unavailable observation."""

    def __init__(self, code, message, *, path=None, bytes_read=0, **details):
        super().__init__(message)
        self.code = code
        self.path = path
        self.bytes_read = bytes_read
        self.details = details

    def record(self):
        result = {"code": self.code, "message": str(self)[:300]}
        if self.path is not None:
            result["path"] = self.path
        result.update(self.details)
        return result


def _integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ContextError("INVALID_INPUT", f"{name} must be an integer from {low} to {high}")
    return value


def _boolean(value, name):
    if type(value) is not bool:
        raise ContextError("INVALID_INPUT", f"{name} must be boolean")
    return value


def _relative(value, *, dot=False):
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise ContextError("INVALID_PATH", "Use a nonempty relative POSIX path")
    p = PurePosixPath(value)
    if p.is_absolute() or ".." in p.parts or len(p.parts) > MAX_DEPTH:
        raise ContextError("INVALID_PATH", "Absolute paths, parent traversal, or excessive depth are not allowed")
    if any(part in (".git", ".hg", ".svn") for part in p.parts):
        raise ContextError("INVALID_PATH", "Version-control metadata is outside the source selection")
    name = p.as_posix()
    if name == "." and not dot:
        raise ContextError("INVALID_PATH", "Select a file")
    if len(os.fsencode(name)) > 4096:
        raise ContextError("INVALID_PATH", "Path exceeds 4096 bytes")
    return name


def _paths(paths):
    if paths is None:
        return ["."]
    if not isinstance(paths, (list, tuple)) or not 1 <= len(paths) <= MAX_PATHS:
        raise ContextError("INVALID_INPUT", "paths must contain from 1 to 128 literal paths")
    return sorted(set(_relative(p, dot=True) for p in paths))


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _expected(value):
    if value is not None and (not isinstance(value, str) or len(value) != 64
                              or any(c not in "0123456789abcdef" for c in value)):
        raise ContextError("INVALID_INPUT", "expected_sha256 must be a lowercase SHA-256 digest")
    return value


def json_bytes(value):
    """The CLI wire representation, including its trailing newline."""
    return (json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"),
                       allow_nan=False) + "\n").encode("utf-8")


def _signature(st):
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns


class _Root:
    def __init__(self, value, *, strict_path=False):
        if not isinstance(value, (str, os.PathLike)):
            raise ContextError("INVALID_INPUT", "root must be a directory path")
        if (os.name != "posix" or not hasattr(os, "O_NOFOLLOW")
                or os.open not in os.supports_dir_fd or os.stat not in os.supports_dir_fd):
            raise ContextError("UNSUPPORTED_PLATFORM", "Descriptor-relative no-follow reads require a supported POSIX host")
        try:
            self.path = Path(value).resolve(strict=True)
            if strict_path and self.path != Path(value).absolute():
                raise ContextError("UNSAFE_PATH", "An instruction ancestor changed to a symlink")
            if not self.path.is_dir():
                raise ContextError("INVALID_ROOT", "root is not a directory")
            # Open every resolved ancestor with no-follow, not just the last one.
            current = os.open(self.path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                for part in self.path.parts[1:]:
                    next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                      dir_fd=current)
                    os.close(current)
                    current = next_fd
            except BaseException:
                os.close(current)
                raise
            self.fd = current
            self.identity = (os.fstat(current).st_dev, os.fstat(current).st_ino)
        except OSError as exc:
            raise ContextError("INVALID_ROOT", f"Cannot open root: {exc.strerror}") from exc

    def __enter__(self):
        return self

    def __exit__(self, *_):
        os.close(self.fd)

    def parent(self, name):
        name = _relative(name)
        parts = PurePosixPath(name).parts
        current = os.dup(self.fd)
        try:
            for part in parts[:-1]:
                following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                    dir_fd=current)
                os.close(current)
                current = following
            return current, parts[-1]
        except BaseException:
            os.close(current)
            raise

    def lstat(self, name):
        if name == ".":
            return os.fstat(self.fd)
        parent, leaf = self.parent(name)
        try:
            return os.stat(leaf, dir_fd=parent, follow_symlinks=False)
        finally:
            os.close(parent)

    def directory(self, name):
        if name == ".":
            return os.dup(self.fd)
        parent, leaf = self.parent(name)
        try:
            return os.open(leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        finally:
            os.close(parent)


def _io_error(exc, name, *, bytes_read=0):
    if isinstance(exc, FileNotFoundError):
        code = "MISSING"
    elif isinstance(exc, PermissionError):
        code = "UNREADABLE"
    elif exc.errno in (20, 40):
        code = "UNSAFE_PATH"
    else:
        code = "IO_ERROR"
    return ContextError(code, "File could not be safely observed", path=name,
                        bytes_read=bytes_read)


def _read_source(root, name, max_bytes, expected_sha256=None):
    """Read one stable regular file; never expose contents on a detected race."""
    name = _relative(name)
    parent = descriptor = None
    data = bytearray()
    try:
        parent, leaf = root.parent(name)
        descriptor = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ContextError("NOT_REGULAR", "Only regular files are read", path=name)
        if before.st_size > max_bytes:
            raise ContextError("FILE_TOO_LARGE", "File exceeds the read budget", path=name,
                               size_bytes=before.st_size)
        while len(data) < max_bytes:
            part = os.read(descriptor, min(65536, max_bytes - len(data)))
            if not part:
                break
            data.extend(part)
        after = os.fstat(descriptor)
        # Rewalk from the root to notice a replaced file or intermediate directory.
        current = root.lstat(name)
        if (_signature(before) != _signature(after) or _signature(after) != _signature(current)
                or len(data) != after.st_size):
            raise ContextError("CHANGED_DURING_READ", "File identity or content changed while reading",
                               path=name, bytes_read=len(data))
        digest = _sha(data)
        if expected_sha256 is not None and digest != expected_sha256:
            raise ContextError("STALE_SOURCE", "Content does not match the expected digest",
                               path=name, bytes_read=len(data), observed_sha256=digest)
        return bytes(data), {"path": name, "sha256": digest, "size_bytes": len(data)}
    except OSError as exc:
        raise _io_error(exc, name, bytes_read=len(data)) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if parent is not None:
            os.close(parent)


def _text(data, name):
    if b"\x00" in data:
        raise ContextError("BINARY_FILE", "NUL-containing files are not text", path=name)
    try:
        encoding = (tokenize.detect_encoding(io.BytesIO(data).readline)[0]
                    if name.endswith(".py") else "utf-8-sig")
        return data.decode(encoding), encoding
    except (UnicodeError, SyntaxError, LookupError) as exc:
        raise ContextError("TEXT_ENCODING", "Text encoding is invalid or unsupported", path=name) from exc


def _lines(text):
    if not text:
        return []
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    if normalized.endswith("\n"):
        lines.pop()
    return lines


def _base(operation, root):
    return {"schema_version": SCHEMA_VERSION, "tool_version": VERSION, "operation": operation,
            "root": str(root.path), "status": "complete", "records": [], "errors": [],
            "coverage": {"complete": True, "error_count": 0, "omitted_output_records": 0},
            "freshness": {"mode": "live-read", "atomic_snapshot": False,
                          "root_identity": {"device": root.identity[0], "inode": root.identity[1]}},
            "metrics": {"read_bytes": 0, "output_bytes": 0, "units": "bytes, not tokens"}}


def _error(result, exc):
    result["coverage"]["complete"] = False
    result["coverage"]["error_count"] += 1
    if len(result["errors"]) < MAX_ERRORS:
        result["errors"].append(exc.record())


def _finish(result, max_output_bytes):
    _integer(max_output_bytes, "max_output_bytes", 1024, MAX_OUTPUT_BYTES)
    result["status"] = "complete" if result["coverage"]["complete"] else "partial"
    if result["coverage"]["error_count"] and not result["records"]:
        result["status"] = "error"
    def measured_size():
        # Account for the size of the size field itself.
        for _ in range(4):
            size = len(json_bytes(result))
            if size == result["metrics"]["output_bytes"]:
                break
            result["metrics"]["output_bytes"] = size
        return len(json_bytes(result))

    if measured_size() <= max_output_bytes:
        return result
    result["coverage"]["complete"] = False
    result["status"] = "partial"
    original = result["records"]
    omitted = result["coverage"]["omitted_output_records"]
    low, high = 0, len(original)
    while low < high:
        middle = (low + high + 1) // 2
        result["records"] = original[:middle]
        result["coverage"]["omitted_output_records"] = omitted + len(original) - middle
        if measured_size() <= max_output_bytes:
            low = middle
        else:
            high = middle - 1
    result["records"] = original[:low]
    result["coverage"]["omitted_output_records"] = omitted + len(original) - low
    while measured_size() > max_output_bytes and result["errors"]:
        result["errors"].pop()
    if measured_size() > max_output_bytes:
        raise ContextError("OUTPUT_LIMIT", "Budget is too small for the result envelope; raise max_output_bytes")
    return result


def _git(root, arguments, *, max_bytes=MAX_GIT_BYTES, timeout=5):
    """Fixed arguments only; bounded stdout, discarded stderr, finite child lifetime."""
    executable = shutil.which("git")
    if not executable:
        return {"status": "unavailable", "data": b""}
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_TERMINAL_PROMPT"] = "0"
    anchor = f"/proc/self/fd/{root.fd}"
    # The qualified Linux host has /proc; refuse an unsafe path fallback.
    if not os.path.isdir(anchor):
        return {"status": "safe_anchor_unavailable", "data": b""}
    command = [executable, "--literal-pathspecs", "-c", "core.fsmonitor=false", "-c",
               "core.hooksPath=/dev/null", "-C", anchor, *arguments]
    try:
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                env=env, pass_fds=(root.fd,))
    except OSError:
        return {"status": "unavailable", "data": b""}
    captured = bytearray()
    read_errors = []

    def collect():
        try:
            while len(captured) <= max_bytes:
                chunk = proc.stdout.read(min(65536, max_bytes + 1 - len(captured)))
                if not chunk:
                    return
                captured.extend(chunk)
        except OSError as exc:
            read_errors.append(type(exc).__name__)

    reader = threading.Thread(target=collect, daemon=True)
    reader.start()
    reader.join(timeout)
    expired = reader.is_alive()
    overflow = len(captured) > max_bytes
    if expired or overflow:
        proc.kill()
    try:
        proc.wait(timeout=1)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    reader.join(1)
    proc.stdout.close()
    status = ("timeout" if expired else "output_limit" if overflow else
              "read_error" if read_errors else "ok" if proc.returncode == 0 else "not_repository")
    return {"status": status, "data": bytes(captured[:max_bytes]), "exit_code": proc.returncode}


def _selected(name, paths):
    return any(p == "." or name == p or name.startswith(p + "/") for p in paths)


def _excluded(name, include_hidden, exclude_dirs):
    parts = PurePosixPath(name).parts
    if any(p in exclude_dirs for p in parts):
        return "excluded_name"
    if not include_hidden and any(p.startswith(".") for p in parts):
        return "hidden"
    return None


def _inventory(root, paths, limit, respect_gitignore, include_hidden, exclude_dirs):
    selection = _paths(paths)
    _integer(limit, "limit", 1, MAX_ENTRIES)
    _boolean(respect_gitignore, "respect_gitignore")
    _boolean(include_hidden, "include_hidden")
    if not isinstance(exclude_dirs, (tuple, list)) or len(exclude_dirs) > 128:
        raise ContextError("INVALID_INPUT", "exclude_dirs must be a bounded list of directory basenames")
    excluded = set(exclude_dirs) | {".git", ".hg", ".svn"}
    if any(not isinstance(n, str) or not n or "/" in n or "\\" in n for n in excluded):
        raise ContextError("INVALID_INPUT", "exclude_dirs must contain basenames")
    result = _base("inventory", root)
    coverage = result["coverage"]
    coverage.update({"paths": selection, "mode": "filesystem", "git_status": "not_requested",
                     "respect_gitignore_requested": respect_gitignore, "gitignore_applied": False,
                     "include_hidden": include_hidden, "excluded_names": sorted(excluded),
                     "visited_entries": 0, "entry_limit": MAX_ENTRIES, "file_limit": limit,
                     "skipped": {}, "absence_scope": "selected paths and reported filters only"})
    skipped = Counter()
    records = {}
    for name in selection:
        if name != ".":
            try:
                root.lstat(name)
            except OSError as exc:
                _error(result, _io_error(exc, name))

    def add(name):
        reason = _excluded(name, include_hidden, excluded)
        if reason:
            skipped[reason] += 1
            return
        try:
            st = root.lstat(name)
            if stat.S_ISLNK(st.st_mode):
                raise ContextError("SYMLINK_SKIPPED", "Symlinks are not followed", path=name)
            if stat.S_ISREG(st.st_mode):
                records[name] = {"path": name, "size_bytes": st.st_size, "mtime_ns": st.st_mtime_ns}
            elif stat.S_ISDIR(st.st_mode):
                raise ContextError("DIRECTORY_NOT_INDEXED", "Git returned a directory; inspect its root or select filesystem traversal explicitly", path=name)
            else:
                raise ContextError("NOT_REGULAR", "Special files are not selected", path=name)
        except OSError as exc:
            _error(result, _io_error(exc, name))
        except ContextError as exc:
            _error(result, exc)

    probe = _git(root, ["rev-parse", "--is-inside-work-tree"], max_bytes=128) if respect_gitignore else None
    if probe is not None:
        coverage["git_status"] = probe["status"]
    if probe and probe["status"] == "ok" and probe["data"].strip() == b"true":
        listing = _git(root, ["ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", *selection])
        coverage.update({"mode": "git-visible", "git_status": listing["status"],
                         "gitignore_applied": True, "git_output_byte_limit": MAX_GIT_BYTES,
                         "git_timeout_seconds": 5})
        if listing["status"] != "ok":
            _error(result, ContextError("GIT_INCOMPLETE", "Git inventory did not finish", reason=listing["status"]))
        # Drop a final unterminated path if the bounded stream was cut.
        parts = listing["data"].split(b"\x00")
        if parts[-1]:
            parts.pop()
        names = sorted(set(os.fsdecode(p) for p in parts if p))
        if len(names) > MAX_ENTRIES:
            coverage["complete"] = False
        for name in names[:MAX_ENTRIES]:
            coverage["visited_entries"] += 1
            try:
                normalized = _relative(name)
            except ContextError as exc:
                _error(result, exc)
                continue
            if _selected(normalized, selection):
                add(normalized)
            if len(records) > limit:
                coverage["complete"] = False
                break
    else:
        deadline = time.monotonic() + 5
        pending = list(reversed(selection))
        seen = set()
        while pending:
            if coverage["visited_entries"] >= MAX_ENTRIES or time.monotonic() > deadline:
                coverage["complete"] = False
                break
            name = pending.pop()
            if name in seen:
                continue
            seen.add(name)
            reason = None if name == "." else _excluded(name, include_hidden, excluded)
            if reason:
                skipped[reason] += 1
                continue
            try:
                st = root.lstat(name)
                if not stat.S_ISDIR(st.st_mode):
                    add(name)
                else:
                    if len(PurePosixPath(name).parts) >= MAX_DEPTH:
                        _error(result, ContextError("DEPTH_LIMIT", "Directory depth limit reached", path=name))
                        continue
                    descriptor = root.directory(name)
                    try:
                        children = []
                        with os.scandir(descriptor) as stream:
                            while coverage["visited_entries"] < MAX_ENTRIES and time.monotonic() <= deadline:
                                try:
                                    entry = next(stream)
                                except StopIteration:
                                    break
                                coverage["visited_entries"] += 1
                                child = entry.name if name == "." else name + "/" + entry.name
                                children.append(child)
                            else:
                                coverage["complete"] = False
                        pending.extend(sorted(children, reverse=True))
                    finally:
                        os.close(descriptor)
            except OSError as exc:
                _error(result, _io_error(exc, name))
            except ContextError as exc:
                _error(result, exc)
            if len(records) > limit:
                coverage["complete"] = False
                break
    result["records"] = [records[n] for n in sorted(records)[:limit]]
    if len(records) > limit:
        coverage["omitted_file_records"] = len(records) - limit
    coverage["skipped"] = dict(sorted(skipped.items()))
    coverage["returned_files"] = len(result["records"])
    result["freshness"].update({"mode": "live-metadata", "content_hashed": False,
                                "selection_digest": _sha(json_bytes(result["records"]))})
    return result


def inventory(root, paths=None, *, limit=1000, max_output_bytes=131072,
              respect_gitignore=True, include_hidden=False, exclude_dirs=DEFAULT_EXCLUDES):
    with _Root(root) as anchor:
        result = _inventory(anchor, paths, limit, respect_gitignore, include_hidden, exclude_dirs)
        return _finish(result, max_output_bytes)


def _excerpt(line, column=0, width=1000):
    start = max(0, column - width // 3) if len(line) > width else 0
    end = min(len(line), start + width)
    return {"text": line[start:end], "excerpt_start_column": start + 1,
            "excerpt_end_column": end + 1, "line_truncated": start != 0 or end != len(line)}


def _line_prefix(line, byte_budget):
    low, high = 0, len(line)
    while low < high:
        middle = (low + high + 1) // 2
        if len(json_bytes(line[:middle])) <= byte_budget:
            low = middle
        else:
            high = middle - 1
    return _excerpt(line, width=low)


def search(root, query, paths=None, *, limit=100, context=1, max_files=1000,
           max_file_bytes=1048576, max_read_bytes=16777216, max_output_bytes=65536,
           respect_gitignore=True, include_hidden=False, case_sensitive=True,
           exclude_dirs=DEFAULT_EXCLUDES):
    if not isinstance(query, str) or not query or len(query) > 4096 or "\n" in query or "\r" in query:
        raise ContextError("INVALID_INPUT", "query must be a single-line literal of 1 to 4096 characters")
    _integer(limit, "limit", 1, MAX_ENTRIES)
    _integer(context, "context", 0, 10)
    _integer(max_file_bytes, "max_file_bytes", 1, MAX_FILE_BYTES)
    _integer(max_read_bytes, "max_read_bytes", 1, MAX_READ_BYTES)
    _boolean(case_sensitive, "case_sensitive")
    with _Root(root) as anchor:
        found = _inventory(anchor, paths, max_files, respect_gitignore, include_hidden, exclude_dirs)
        result = _base("search", anchor)
        result["coverage"] = found["coverage"]
        result["errors"] = found["errors"]
        result["coverage"].update({"searched_files": 0, "match_limit": limit,
                                    "matches_are": "first literal match per matching line"})
        result["query"] = {"literal": query, "case_sensitive": case_sensitive}
        sources = []
        stop = False
        for file in found["records"]:
            remaining = max_read_bytes - result["metrics"]["read_bytes"]
            if remaining <= 0:
                _error(result, ContextError("READ_LIMIT", "Total source read budget exhausted"))
                break
            try:
                data, source = _read_source(anchor, file["path"], min(max_file_bytes, remaining))
                result["metrics"]["read_bytes"] += len(data)
                text, _ = _text(data, file["path"])
                result["coverage"]["searched_files"] += 1
                sources.append(source)
                lines = _lines(text)
                for number, line in enumerate(lines, 1):
                    haystack = line if case_sensitive else line.casefold()
                    needle = query if case_sensitive else query.casefold()
                    offset = haystack.find(needle)
                    if offset < 0:
                        continue
                    if len(result["records"]) == limit:
                        result["coverage"]["complete"] = False
                        result["coverage"]["match_limit_reached"] = True
                        stop = True
                        break
                    if not case_sensitive:
                        # Unicode casefold expansion changes offsets; map the
                        # transformed prefix back to the original code points.
                        count = 0
                        original = 0
                        for original, character in enumerate(line):
                            if count + len(character.casefold()) > offset:
                                break
                            count += len(character.casefold())
                        offset = original
                    surroundings = []
                    for n in range(max(1, number - context), min(len(lines), number + context) + 1):
                        if n != number:
                            surroundings.append({"line": n, **_excerpt(lines[n - 1], width=240)})
                    result["records"].append({**source, "line": number, "column": offset + 1,
                                               **_excerpt(line, offset), "context": surroundings})
                if stop:
                    break
            except ContextError as exc:
                result["metrics"]["read_bytes"] += exc.bytes_read
                _error(result, exc)
        result["freshness"]["sources_digest"] = _sha(json_bytes(sources))
        result["freshness"]["source_count"] = len(sources)
        result["coverage"]["matched_lines_returned_before_output_limit"] = len(result["records"])
        return _finish(result, max_output_bytes)


def _slice(anchor, path, start, end, max_file_bytes, max_output_bytes, expected_sha256):
    name = _relative(path)
    _integer(start, "start", 1, 10**9)
    if end is not None:
        _integer(end, "end", start, 10**9)
    _integer(max_file_bytes, "max_file_bytes", 1, MAX_FILE_BYTES)
    _expected(expected_sha256)
    result = _base("read", anchor)
    result["selection"] = {"path": name, "start": start, "end": end}
    try:
        data, source = _read_source(anchor, name, max_file_bytes, expected_sha256)
        result["metrics"]["read_bytes"] = len(data)
        text, encoding = _text(data, name)
        lines = _lines(text)
        final = len(lines) if end is None else min(end, len(lines))
        result["source"] = {**source, "encoding": encoding, "line_count": len(lines)}
        if start > len(lines) and not (start == 1 and not lines):
            raise ContextError("RANGE_OUTSIDE_FILE", "start is beyond the last source line", path=name)
        line_budget = max(1, max_output_bytes - len(json_bytes(result)) - 256)
        for number in range(start, final + 1):
            excerpt = _line_prefix(lines[number - 1], line_budget)
            if excerpt["line_truncated"]:
                result["coverage"]["complete"] = False
            result["records"].append({"line": number, **excerpt})
    except ContextError as exc:
        result["metrics"]["read_bytes"] += exc.bytes_read
        _error(result, exc)
    return _finish(result, max_output_bytes)


def read_slice(root, path, start=1, end=None, *, max_file_bytes=1048576,
               max_output_bytes=65536, expected_sha256=None):
    with _Root(root) as anchor:
        return _slice(anchor, path, start, end, max_file_bytes, max_output_bytes, expected_sha256)


def symbols(root, path, *, limit=200, max_file_bytes=1048576,
            max_output_bytes=65536, expected_sha256=None):
    name = _relative(path)
    _integer(limit, "limit", 1, MAX_ENTRIES)
    _integer(max_file_bytes, "max_file_bytes", 1, MAX_FILE_BYTES)
    _expected(expected_sha256)
    with _Root(root) as anchor:
        result = _base("symbols", anchor)
        result["coverage"].update({"language": "python", "semantic_resolution": False,
                                    "scope": "syntax definitions and imports; no resolved references or call graph"})
        if not name.endswith(".py"):
            _error(result, ContextError("UNSUPPORTED_LANGUAGE", "AST symbols currently support .py source", path=name))
            return _finish(result, max_output_bytes)
        try:
            data, source = _read_source(anchor, name, max_file_bytes, expected_sha256)
            result["metrics"]["read_bytes"] = len(data)
            _text(data, name)
            tree = ast.parse(data, filename=name)
            result["source"] = source
            pending = [(tree, ())]
            visited = 0
            while pending:
                node, scope = pending.pop()
                visited += 1
                if visited > 50000:
                    _error(result, ContextError("AST_NODE_LIMIT", "AST traversal limit reached", path=name))
                    break
                child_scope = scope
                record = None
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    kind = "class" if isinstance(node, ast.ClassDef) else "async_function" if isinstance(node, ast.AsyncFunctionDef) else "function"
                    first = min([node.lineno] + [d.lineno for d in node.decorator_list])
                    record = {"kind": kind, "name": node.name, "qualified_name": ".".join((*scope, node.name)),
                              "line": node.lineno, "start_line": first, "end_line": node.end_lineno}
                    child_scope = (*scope, node.name)
                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    record = {"kind": "import", "line": node.lineno, "end_line": node.end_lineno,
                              "module": getattr(node, "module", None), "level": getattr(node, "level", 0),
                              "names": [{"name": a.name, "alias": a.asname} for a in node.names]}
                if record is not None:
                    if len(result["records"]) == limit:
                        result["coverage"]["complete"] = False
                        result["coverage"]["symbol_limit_reached"] = True
                        break
                    result["records"].append(record)
                pending.extend((child, child_scope) for child in reversed(list(ast.iter_child_nodes(node))))
            result["coverage"]["visited_ast_nodes"] = min(visited, 50000)
        except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
            if isinstance(exc, ContextError):
                result["metrics"]["read_bytes"] += exc.bytes_read
                _error(result, exc)
            else:
                _error(result, ContextError("PARSE_ERROR", "Python source could not be parsed", path=name,
                                             line=getattr(exc, "lineno", None)))
        return _finish(result, max_output_bytes)


def bundle(root, selections, *, max_output_bytes=65536, max_file_bytes=1048576):
    if not isinstance(selections, list) or not 1 <= len(selections) <= MAX_PATHS:
        raise ContextError("INVALID_INPUT", "selections must contain from 1 to 128 source slices")
    _integer(max_file_bytes, "max_file_bytes", 1, MAX_FILE_BYTES)
    with _Root(root) as anchor:
        result = _base("bundle", anchor)
        result["coverage"]["requested_slices"] = len(selections)
        for selection in selections:
            if not isinstance(selection, dict) or set(selection) != {"path", "start", "end", "sha256"}:
                raise ContextError("INVALID_INPUT", "Each slice needs exactly path, start, end, sha256")
            if selection["sha256"] is None:
                raise ContextError("INVALID_INPUT", "Bundle slices require an observed SHA-256 digest")
            remaining = MAX_READ_BYTES - result["metrics"]["read_bytes"]
            if remaining <= 0:
                _error(result, ContextError("READ_LIMIT", "Bundle total read limit exhausted"))
                break
            item = _slice(anchor, selection["path"], selection["start"], selection["end"],
                          min(max_file_bytes, remaining), max_output_bytes, selection["sha256"])
            result["metrics"]["read_bytes"] += item["metrics"]["read_bytes"]
            if not item["coverage"]["complete"]:
                result["coverage"]["complete"] = False
            for error in item["errors"]:
                _error(result, ContextError(error["code"], error["message"], path=error.get("path")))
            if "source" in item:
                result["records"].append({"selection": selection, "source": item["source"],
                                           "lines": item["records"], "complete": item["coverage"]["complete"]})
        return _finish(result, max_output_bytes)


def _instruction_candidates(root, selected, inventory_records):
    candidates = {}
    for directory in reversed((root.path, *root.path.parents)):
        candidates[str(directory / "AGENTS.md")] = (directory, "ancestor" if directory != root.path else "root")
    for name in selected:
        if name == ".":
            continue
        try:
            is_directory = stat.S_ISDIR(root.lstat(name).st_mode)
        except OSError:
            is_directory = False
        target = root.path / name
        directory = target if is_directory else target.parent
        while directory != root.path and directory.is_relative_to(root.path):
            candidates[str(directory / "AGENTS.md")] = (directory, "selected_ancestor")
            directory = directory.parent
    for file in inventory_records:
        if PurePosixPath(file["path"]).name == "AGENTS.md":
            directory = (root.path / file["path"]).parent
            candidates.setdefault(str(directory / "AGENTS.md"), (directory, "nested_candidate"))
    return sorted(candidates.items(), key=lambda item: (len(Path(item[0]).parts), item[0]))


def _manifest_facts(name, data):
    facts = {}
    if name == "package.json":
        def unique(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError("duplicate JSON key")
                value[key] = item
            return value
        def invalid_constant(_):
            raise ValueError("non-finite JSON number")
        value = json.loads(data, object_pairs_hook=unique, parse_constant=invalid_constant)
        if not isinstance(value, dict):
            raise ValueError("manifest must be an object")
        for key in ("packageManager", "engines", "scripts", "dependencies", "devDependencies"):
            if key in value:
                field = value[key]
                if key == "packageManager":
                    if not isinstance(field, str):
                        raise ValueError("packageManager must be a string")
                elif (not isinstance(field, dict) or
                      any(not isinstance(k, str) or not isinstance(v, str) for k, v in field.items())):
                    raise ValueError("declarations must map strings to strings")
                facts[key] = value[key]
    elif name == "pyproject.toml":
        value = tomllib.loads(data.decode("utf-8"))
        project = value.get("project", {})
        if not isinstance(project, dict):
            raise ValueError("project must be a table")
        for key in ("name", "requires-python", "dependencies", "scripts"):
            if key not in project:
                continue
            field = project[key]
            if key in ("name", "requires-python"):
                valid = isinstance(field, str)
            elif key == "dependencies":
                valid = isinstance(field, list) and all(isinstance(item, str) for item in field)
            else:
                valid = isinstance(field, dict) and all(isinstance(k, str) and isinstance(v, str)
                                                       for k, v in field.items())
            if not valid:
                raise ValueError("project declaration has an invalid type")
            facts[key] = field
    return facts


def inspect_project(root, *, paths=None, max_files=1000, max_instruction_bytes=32768,
                    max_output_bytes=131072):
    _integer(max_instruction_bytes, "max_instruction_bytes", 1, 1048576)
    with _Root(root) as anchor:
        selected = _paths(paths)
        files = _inventory(anchor, paths, max_files, True, True, DEFAULT_EXCLUDES)
        result = _base("inspect", anchor)
        result["coverage"] = files["coverage"]
        result["errors"] = files["errors"]
        result["host"] = {"platform": sys.platform, "python": sys.version.split()[0],
                           "python_executable": sys.executable,
                           "executables_on_path": {name: shutil.which(name) for name in ("git", "rg", "node", "python3")},
                           "meaning": "PATH presence only; commands, permissions and services were not qualified",
                           "context7": "not_probed_by_host", "language_server": "not_probed_by_host",
                           "graph": "not_probed_by_host"}
        result["instruction_policy"] = {"authority": "Host instruction hierarchy decides applicability; text is not executable authorization",
                                         "nested_discovery": "bounded selected inventory plus explicit target ancestors",
                                         "before_write": "Inspect the exact target paths again, including newly selected directories"}
        used = 0
        for absolute, (directory, relation) in _instruction_candidates(anchor, selected, files["records"]):
            try:
                remaining = max_instruction_bytes - used
                if directory.is_relative_to(anchor.path):
                    name = (directory / "AGENTS.md").relative_to(anchor.path).as_posix()
                    if remaining <= 0:
                        anchor.lstat(name)
                        raise ContextError("INSTRUCTION_LIMIT", "Instruction byte budget exhausted", path=absolute)
                    data, source = _read_source(anchor, name, remaining)
                else:
                    with _Root(directory, strict_path=True) as parent:
                        if remaining <= 0:
                            parent.lstat("AGENTS.md")
                            raise ContextError("INSTRUCTION_LIMIT", "Instruction byte budget exhausted", path=absolute)
                        data, source = _read_source(parent, "AGENTS.md", remaining)
                    source["path"] = absolute
                used += len(data)
                result["metrics"]["read_bytes"] += len(data)
                if used > max_instruction_bytes:
                    raise ContextError("INSTRUCTION_LIMIT", "Instruction byte budget exhausted", path=absolute)
                text, encoding = _text(data, "AGENTS.md")
                result["records"].append({"kind": "instructions", **source, "scope": str(directory),
                                           "relation": relation, "encoding": encoding, "text": text})
            except ContextError as exc:
                result["metrics"]["read_bytes"] += exc.bytes_read
                used += exc.bytes_read
                if exc.code != "MISSING":
                    _error(result, exc)
            except OSError as exc:
                if not isinstance(exc, FileNotFoundError):
                    _error(result, _io_error(exc, absolute))
        manifests = {p["path"] for p in files["records"] if PurePosixPath(p["path"]).name in MANIFESTS}
        # Root manifests remain relevant when the caller selects a nested file.
        for name in sorted(MANIFESTS):
            try:
                observed = anchor.lstat(name)
                if stat.S_ISREG(observed.st_mode):
                    manifests.add(name)
                elif stat.S_ISLNK(observed.st_mode):
                    _error(result, ContextError("SYMLINK_SKIPPED", "Manifest symlinks are not followed", path=name))
            except OSError:
                pass
        if len(manifests) > 32:
            result["coverage"]["complete"] = False
            result["coverage"]["manifest_limit_reached"] = True
        for name in sorted(manifests)[:32]:
            try:
                data, source = _read_source(anchor, name, 1048576)
                result["metrics"]["read_bytes"] += len(data)
                facts = _manifest_facts(PurePosixPath(name).name, data)
                result["records"].append({"kind": "manifest", **source, "declared": facts,
                                           "commands_executed": False,
                                           "dependency_note": "Manifest ranges are declarations, not verified resolved versions"})
            except ContextError as exc:
                result["metrics"]["read_bytes"] += exc.bytes_read
                _error(result, exc)
            except (ValueError, UnicodeError, RecursionError) as exc:
                _error(result, ContextError("MANIFEST_PARSE", "Manifest data could not be parsed unambiguously", path=name))
        result["freshness"]["profile_digest"] = _sha(json_bytes({"host": result["host"],
            "root": str(anchor.path), "root_identity": anchor.identity, "tool_version": VERSION,
            "sources": [{"kind": r["kind"], "path": r["path"], "sha256": r["sha256"]} for r in result["records"]],
            "selection": selected, "inventory_digest": files["freshness"]["selection_digest"]}))
        result["freshness"]["use"] = "Recompute for the same paths before relying on the profile after changes"
        return _finish(result, max_output_bytes)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--max-output-bytes", type=int, default=65536)
    commands = parser.add_subparsers(dest="operation", required=True)
    for name in ("inventory", "search", "inspect"):
        command = commands.add_parser(name)
        command.add_argument("--path", action="append", dest="paths")
        if name != "inspect":
            command.add_argument("--include-hidden", action="store_true")
            command.add_argument("--no-gitignore", action="store_true")
            command.add_argument("--include-excluded", action="store_true")
            command.add_argument("--limit", type=int, default=100 if name == "search" else 1000)
        if name == "search":
            command.add_argument("query")
            command.add_argument("--context", type=int, default=1)
            command.add_argument("--ignore-case", action="store_true")
            command.add_argument("--max-files", type=int, default=1000)
            command.add_argument("--max-file-bytes", type=int, default=1048576)
            command.add_argument("--max-read-bytes", type=int, default=16777216)
        if name == "inspect":
            command.add_argument("--max-files", type=int, default=1000)
            command.add_argument("--max-instruction-bytes", type=int, default=32768)
    for name in ("read", "symbols"):
        command = commands.add_parser(name)
        command.add_argument("path")
        command.add_argument("--expected-sha256")
        command.add_argument("--max-file-bytes", type=int, default=1048576)
        if name == "read":
            command.add_argument("--start", type=int, default=1)
            command.add_argument("--end", type=int)
        else:
            command.add_argument("--limit", type=int, default=200)
    command = commands.add_parser("bundle")
    command.add_argument("selections", help="JSON array of {path,start,end,sha256}; interpreted only as data")
    command.add_argument("--max-file-bytes", type=int, default=1048576)
    args = parser.parse_args(argv)
    try:
        shared = {"max_output_bytes": args.max_output_bytes}
        if args.operation == "inventory":
            result = inventory(args.root, args.paths, limit=args.limit, include_hidden=args.include_hidden,
                               respect_gitignore=not args.no_gitignore,
                               exclude_dirs=[] if args.include_excluded else DEFAULT_EXCLUDES, **shared)
        elif args.operation == "search":
            result = search(args.root, args.query, args.paths, limit=args.limit, context=args.context,
                            include_hidden=args.include_hidden, respect_gitignore=not args.no_gitignore,
                            case_sensitive=not args.ignore_case, max_files=args.max_files,
                            max_file_bytes=args.max_file_bytes, max_read_bytes=args.max_read_bytes,
                            exclude_dirs=[] if args.include_excluded else DEFAULT_EXCLUDES, **shared)
        elif args.operation == "inspect":
            result = inspect_project(args.root, paths=args.paths, max_files=args.max_files,
                                     max_instruction_bytes=args.max_instruction_bytes, **shared)
        elif args.operation == "read":
            result = read_slice(args.root, args.path, args.start, args.end,
                                expected_sha256=args.expected_sha256, max_file_bytes=args.max_file_bytes, **shared)
        elif args.operation == "symbols":
            result = symbols(args.root, args.path, expected_sha256=args.expected_sha256,
                             max_file_bytes=args.max_file_bytes, limit=args.limit, **shared)
        else:
            if len(args.selections.encode("utf-8")) > 65536:
                raise ContextError("INVALID_INPUT", "Bundle selection JSON exceeds 65536 bytes")
            result = bundle(args.root, json.loads(args.selections), max_file_bytes=args.max_file_bytes, **shared)
        sys.stdout.buffer.write(json_bytes(result))
        return 0 if result["status"] == "complete" else 3
    except (ContextError, json.JSONDecodeError) as exc:
        error = exc.record() if isinstance(exc, ContextError) else {"code": "INVALID_JSON", "message": "Malformed selection JSON"}
        sys.stdout.buffer.write(json_bytes({"schema_version": 1, "status": "error", "error": error}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
