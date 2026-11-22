# Runtime verification

## Contents

- [Purpose and trust](#purpose-and-trust)
- [Check catalog](#check-catalog)
- [Execution and receipts](#execution-and-receipts)
- [Test adapters](#test-adapters)
- [Integration and acceptance](#integration-and-acceptance)
- [Qualification](#qualification)

## Purpose and trust

Use the controller's prepared check operation to run a reviewed catalog entry. `scripts/contour_verify.py` supervises the process; `scripts/contour_unittest.py` is its built-in Python test adapter. Ordinary task agents select a check ID. They do not supply a new shell command, generate a replacement runner, or edit the catalog to turn a failure into a pass.

The qualified local profile is Linux with CPython 3.12.14. Every receipt says `origin: LOCAL_OBSERVED` and `acceptance: NOT_DECIDED`. The parent process observes invocation, completion, outputs, and adapter results. The process group, output limits, hashes, journal, and SQLite state are not a sandbox or authentication service. An adversarial process under the same operating-system user can alter reports, controls, and the ledger. Technical protection requires an independently qualified host or protected CI control plane.

An authorized test or build executes project code and can have effects. The catalog must identify permitted commands and the controller must retain their authority, budgets, and dependencies. No host credential environment is inherited. Explicit project environment values are reviewed configuration; they are not automatically secret-safe merely because they appear in a catalog. The runner does not dump their values into receipts, but project code can print them.

## Check catalog

`validate_check(check)` returns the normalized catalog entry. The controller must use this same validator during setup and execution. Unknown fields, bad types, nonfinite budgets, parent traversal, reserved environment keys, direct shell invocations, and inline interpreter programs are rejected. Catalog validation does not turn arbitrary project code into trusted code.

| Field | Meaning |
|---|---|
| `id` | Stable nonempty check ID |
| `kind` | `unittest`, `junit`, or `process` |
| `cwd` | Relative product working directory; defaults to `.`; symlinks and the reserved root `.contour/` are rejected |
| `timeout_seconds` | Process supervision budget, from 0.05 to 3600 seconds; defaults to 120 |
| `output_limit_bytes` | Combined retained stdout/stderr limit, from 1024 to 16777216 bytes; defaults to 1048576 |
| `snapshot_paths` | Explicit complete selection of material inputs; defaults to `["."]` |
| `env` | Reviewed explicit string map; defaults to `{}`; no automatic dotenv import |

The child receives a prepared `PATH`, locale, `PYTHONDONTWRITEBYTECODE`, and per-run `TMPDIR`, plus approved `env` entries. It does not inherit host credentials or repurpose `HOME`. The runner reserves `PATH`, `HOME`, temporary-directory variables, and common interpreter-injection environment variables. The snapshot selects actual product bytes and declares the root `.contour/` exclusion. Other ignored inputs remain included by an explicit `.` selection; external services, database state, runtime dependencies, and secrets need project-specific environment qualification.

### Python unittest

Additional fields are `discover` (default `tests`, relative to the project root), `pattern` (default `test*.py`), and `minimum_passed` (positive integer, default 1). The operation constructs the prepared worker argv itself. The reviewed in-project `cwd` is an import location before the outer root, so a nested product imports its own modules; `discover` remains root-relative and cannot select reserved root `.contour/`. Discovery inputs must be included in the candidate snapshot. Include material imports, fixtures, configuration, lockfiles, and other oracle inputs in the project selection.

```json
{
  "id": "unit-tests",
  "kind": "unittest",
  "discover": "tests",
  "pattern": "test*.py",
  "minimum_passed": 1,
  "timeout_seconds": 120,
  "snapshot_paths": ["."]
}
```

### Portable JUnit-producing check

Additional fields are `argv`, `dialect`, `producer`, and `minimum_passed`. `argv` is an exact reviewed argument array whose executable is an absolute path. It contains exactly one standalone `{report}` argument. The controller replaces that argument with a new report path inside the exclusive run directory. No other per-run argument injection is available. Use `dialect: junit-leaf-v1` and record the actual qualified producer/version in `producer`.

The profile accepts a root `testsuite`, or a root `testsuites` containing leaf suites. Each suite has integer `tests`, `failures`, `errors`, and `skipped` counters matching its actual testcase elements. Optional root aggregate counters must match the sum. Every testcase has a unique `(classname, name)` identity, and zero or one failure/error/skipped outcome. Simple properties and captured-output elements are permitted. Nested suites, direct root testcases, multiple outcomes, unknown outcome structures, or unsupported status attributes are rejected.

The report is limited to 1 MiB, 50,000 XML nodes, 1,000 suites, and 10,000 testcases. Only UTF-8 is accepted. DTD and entity declarations, symlink reports, malformed XML, and inconsistent counters fail closed. `pytest.xfail` skipped elements are retained as expected failures. A producer's omitted information cannot be reconstructed: for example, a non-strict unexpected success represented as an ordinary pass needs the producer's own stricter configuration or a different qualified adapter.

Node 24.22.3's native `node:test` JUnit reporter was exercised with real assertions inside leaf `describe` suites, for both passing and failing cases. Its direct top-level test format is outside this dialect. This qualification does not assert compatibility with every Node report layout, pytest version, or other JUnit producer. Qualify the exact producer/configuration used by the project through a successful run and deliberate failure/empty/skip cases.

### Build or lint process

Additional fields are an exact `argv` array and `purpose: build` or `purpose: lint`. The executable must be an absolute reviewed path. There is no shell interpretation of an argument string. A process receipt always has `tests: null`; exit zero does not establish a test count, domain acceptance, security clearance, or permission to release.

## Execution and receipts

The module interface for controller integrators is:

```python
validate_check(check: dict) -> dict
snapshot_candidate(project_root: Path, project_id: str, selection_paths=None) -> dict
supervise(check: dict, project_root: Path, run_dir: Path, binding: dict) -> dict
```

Use the same `snapshot_paths` at admission and execution. `snapshot_candidate` returns the `contour_ops.snapshot` format; its default selection is `["."]`, including non-Git projects. The root `.contour/` exclusion is recorded in `selection.excluded_subtrees` and the candidate digest. A nested product directory such as `src/.contour/` remains an ordinary input.

Use a fresh `run_dir` under `.contour/runtime/runs/`. The runner also supports existing external control locations, but rejects ordinary product directories and symlinked storage ancestors. It creates the run directory exclusively and refuses reuse. A creation race does not authorize overwriting another operation's receipt.

The controller supplies its current binding, including `project_id`, `candidate_digest`, `profile_digest`, and unique `criterion_ids`, together with task, worker, generation, operation, and attempt identity. The runner preserves this binding. Criterion semantics belong to the controller's task/profile contract; `criteria_digest` in the receipt hashes the criterion IDs, not an independently invented copy of their definitions.

Before spawning, the runner records the candidate and a durable `intent.json`. It then records `launch.json`, captures bounded stdout/stderr, and collects the adapter report. After execution, it snapshots the candidate again and compares runner-control bytes. Current changes to either make the result inapplicable. A before/after hash comparison cannot detect an adversarial transient edit followed by restoration; that requires real isolation.

The runner drains both streams concurrently. On timeout, excessive output, or lingering same-group processes, it terminates the group, applies a bounded stronger termination step, reaps its direct child, and observes the Linux process group. Zombie descendants are recorded but cannot continue executing. An escaped descendant is outside process-group containment. Where cleanup cannot be verified, the receipt is incomplete and effects remain unresolved.

`duration_seconds` is the total local operation duration, including observations and cleanup; `timeout_seconds` is the process budget. Reserve observation and cleanup overhead as well as the process budget. Record actual budget overruns instead of truncating them to the reservation. Filesystem observations and process creation have their underlying platform limits; the runner does not promise universal hard real-time scheduling.

Each run directory contains intent, launch if observed, before/after candidate observations where available, two bounded logs, log metadata, the test report where applicable, and `receipt.json`. Logs include paths, retained-byte hashes, retained/observed byte counts, and a `bytes` alias for retained bytes. These records contain project paths and output; preserve them according to project data-handling rules.

| Receipt status | Meaning |
|---|---|
| `PASS` | The completed check met its prepared adapter and binding rules |
| `FAIL` | Observed exit or test outcomes failed the check |
| `INCOMPLETE` | Timeout, interruption, partial execution, insufficient successful tests, or changed candidate/control inputs |
| `ERROR` | Invalid setup, access failure, missing/malformed report, or failed recording |

`completed` records verified completion of the check path; it is false for setup errors and interrupted execution. A completed run with all tests skipped can still be `INCOMPLETE` because it supplies insufficient test evidence. Prelaunch errors can have empty argv/logs, null candidate fields, and no return code. The controller must record such real failures rather than require a success-shaped receipt.

Intent without a trustworthy completion is an unresolved attempt. An error after launch was attempted remains conservative even if `launch.json` could not be persisted. Inspect process identity and actual effects before retry. Never infer that absence of a final receipt means no process started.

## Test adapters

Both test adapters report `selected`, `succeeded`, `failed`, `errors`, `skipped`, `expected_failures`, and `unexpected_successes`. PASS requires a positive sufficient successful count, no failure/error/unexpected-success outcome, a zero process exit, completion, and stable binding. A selected count alone is not a measure of useful execution.

The unittest worker counts top-level success through `addSuccess`. Successful subtests contribute to a successful containing case; failed subtests remain separate failure observations. Count a skipped top-level selection once even when several of its subtests are skipped; retain the individual skipped-subtest count in `test_details.subtests.skipped`. Never relabel a partially skipped case as a fully successful case. Fixture skips without a started test case remain separately recorded. Subtest failures can outnumber selected cases, so successes are never computed by subtracting failure-list lengths from `testsRun`. Discovery, setup, teardown, early stop, expected failure, and unexpected success stay distinguishable.

No stdout phrase or agent-written PASS JSON becomes a test result. The parent reads only the new adapter report under the allocated run directory. The unittest report includes the runner-generated run ID. The generic JUnit path enforces a fresh report destination and actual command execution; it cannot authenticate a malicious producer that fabricates XML. That is part of the explicit local trust boundary.

## Integration and acceptance

Record every attempt, including failed or interrupted attempts, in the controller's ledger. A green retry on the same basis does not erase an earlier failure. Reconciliation or a new corrected candidate must be explicit. The verification receipt never decides product acceptance.

Recheck the integrated candidate after parallel workers finish. A receipt for an individual worktree does not automatically apply to a merged tree or a changed base branch. Do not replace digest fields to transfer an old result to a new candidate.

Protected CI evidence additionally needs provider-origin, repository, commit/integration candidate, workflow, run attempt, check source, actual reports, and verified control protection. Offline provider JSON remains declared evidence. A hosted status alone does not establish that tests ran; skipped or neutral checks must not satisfy a test criterion. The local runner makes no claim that a remote workflow has executed merely because a workflow file was supplied.

## Qualification

When changing the runner or adding a producer, exercise actual processes and meaningful negative cases: unsuccessful or empty selections, interruption, bounded output, stale inputs and malformed reports. Use the relevant producer on the target host; a skipped check does not establish support. Preserve meaningful behavioral coverage when simplifying checks. Test counts do not establish comparative methodology quality.
