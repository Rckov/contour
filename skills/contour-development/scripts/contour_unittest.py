#!/usr/bin/env python3
"""Prepared unittest worker; its parent owns process observation and the receipt."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import unittest

SCHEMA_VERSION = 1
PROTOCOL = "contour-unittest-v1"


class CountingResult(unittest.TextTestResult):
    """Count successful cases directly: subtest failure lists are not case counts."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.succeeded = 0
        self.skipped_selections = 0
        self.subtests_succeeded = 0
        self.subtests_failed = 0
        self.subtests_skipped = 0
        self.fixture_skips = 0
        self._active_case = None
        self._case_skip_recorded = False

    def startTest(self, test):
        self._active_case = test
        self._case_skip_recorded = False
        super().startTest(test)

    def stopTest(self, test):
        super().stopTest(test)
        self._active_case = None

    def addSkip(self, test, reason):
        if self._active_case is None:
            self.fixture_skips += 1
        else:
            if not self._case_skip_recorded:
                self.skipped_selections += 1
                self._case_skip_recorded = True
            if test is not self._active_case:
                self.subtests_skipped += 1
        super().addSkip(test, reason)

    def addSuccess(self, test):
        if not self._case_skip_recorded:
            self.succeeded += 1
        super().addSuccess(test)

    def addSubTest(self, test, subtest, err):
        if err is None:
            self.subtests_succeeded += 1
        else:
            self.subtests_failed += 1
        super().addSubTest(test, subtest, err)


def run(root, start, pattern, run_id):
    root = Path(root).resolve()
    start = Path(start).resolve()
    if not root.is_dir() or not start.is_dir() or not start.is_relative_to(root):
        raise ValueError("discovery directory must be inside the project root")
    sys.path.insert(0, str(root))
    working = Path.cwd().resolve()
    if working != root and working.is_relative_to(root):
        # -I omits the ambient cwd. Reintroduce only the reviewed in-project
        # working directory, so nested products import their own modules.
        sys.path.insert(0, str(working))
    loader = unittest.TestLoader()
    suite = loader.discover(str(start), pattern=pattern)
    selected = suite.countTestCases()
    result = unittest.TextTestRunner(
        stream=sys.stderr, verbosity=1, resultclass=CountingResult, failfast=False,
    ).run(suite)
    return {
        "schema_version": SCHEMA_VERSION,
        "protocol": PROTOCOL,
        "run_id": run_id,
        "completed": not result.shouldStop,
        "successful": result.wasSuccessful(),
        "executed": result.testsRun,
        "discovery_errors": len(loader.errors),
        "tests": {
            "selected": selected,
            "succeeded": result.succeeded,
            "failed": len(result.failures),
            "errors": len(result.errors),
            "skipped": result.skipped_selections,
            "expected_failures": len(result.expectedFailures),
            "unexpected_successes": len(result.unexpectedSuccesses),
        },
        "subtests": {"succeeded": result.subtests_succeeded, "failed": result.subtests_failed,
                     "skipped": result.subtests_skipped},
        "fixture_skips": result.fixture_skips,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--discover", required=True)
    parser.add_argument("--pattern", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args(argv)
    report = run(args.root, args.discover, args.pattern, args.run_id)
    data = (json.dumps(report, sort_keys=True, allow_nan=False) + "\n").encode()
    # Exclusive creation prevents an old report from being silently overwritten.
    fd = os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    return 0 if report["completed"] and report["successful"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
