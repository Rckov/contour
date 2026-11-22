# Prepared consistency operations

## Contents

- [Purpose and limits](#purpose-and-limits)
- [Catalog](#catalog)
- [Snapshot and scope](#snapshot-and-scope)
- [Evidence and gate](#evidence-and-gate)
- [Resume and effects](#resume-and-effects)
- [Evaluation input formats](#eval-summary-input-formats)
- [Changing the catalog](#changing-the-catalog)

## Purpose and limits

One program, `scripts/contour_ops.py`, provides eight operations using the Python standard library. It does not write product files, execute supplied code or commands, access the network, or run product tests. `doctor` and `snapshot` invoke only fixed Git commands. Operational reports and errors are JSON on stdout; help and version output are plain text. Retain needed reports under the project's `.contour/`, linking existing primary evidence. Output may contain file names and references; do not publish it automatically.

For durable execution state, prepared checks and native-agent assignments, use the optional [local workflow](runtime-workflow.md). Use native Git or host tools to create working copies and integrate changes. For source retrieval use [context operations](runtime-context.md). This program checks supplied data and local observations; it does not own execution state.

The reference environment is CPython 3.12.14 on Linux. Other Python versions and operating systems require their own smoke check. This is a coordinator-managed local profile with a trusted working directory; the program is not a sandbox against a concurrent malicious process, an authorization server, an ownership registry, or a replacement for CI. The coordinator records task states in the chosen source. Invoke project commands and external APIs through their standard tools within current authority.

General invocation: `python3 -B <skill-root>/scripts/contour_ops.py <operation> ...`. `--help` and `<operation> --help` show arguments. From a standalone skill's root, use:

```bash
python3 -B scripts/contour_ops.py --help
```

From the extracted release archive's root, use:

```bash
python3 -B skills/contour-development/scripts/contour_ops.py --help
```

Keep generated reports inside `.contour/`, outside the declared product snapshot. Use fresh report paths without overwriting existing evidence; the calling shell performs `>` redirection, not the operation.

Exit codes: `0` means the operation produced a result according to its purpose; it is **not a universal product PASS**. `1` means an argument, input, or access error. `2` means a detected gap, incomplete observation, or need for review. Interpret the status inside the JSON. Do not treat an error or unknown format as success.

## Catalog

| Operation | Arguments | Result and limit |
|---|---|---|
| `doctor` | `--root` | Known files at the root, Git/HEAD, and Python. Does not recursively discover instructions or check permissions or Context7. |
| `snapshot` | `--root --project-id`, repeatable `--path` | SHA-256 of selected actual files, executable bit, inventory, and limitations. Observation is not atomic and excludes the external environment. |
| `scope` | `--before --after`, repeatable `--allow`, `--protect` | Changes to selected files, scope violations, and control changes. Shared semantic resources require separate inspection. |
| `gate` | `--task --snapshot`, optional `--evidence` | Without evidence, prepares a binding before verification. With evidence, checks identity, states, criteria, completeness, and waivers permitted by the supplied data. Returns `admission: NOT_DECIDED`; it does not accept the product. |
| `handoff` | `--task --record` | Checks handoff structure, preserves unknown effects, processes, and budget, and identifies reconciliation needs. Does not automatically authorize continuation. |
| `api-surface` | `--file`, optional `--before` | Inventories `paths` operations in JSON OpenAPI 3.0-3.2; for 3.2, includes QUERY and `additionalOperations` while preserving method case. Does not perform full schema validation, establish semantic compatibility, or resolve `$ref`. |
| `eval-summary` | `--file` | Descriptive complete or partial observations for whole trajectories and their attempts. Unknown values stay unknown. Does not acquire telemetry or establish statistical superiority. |
| `verify-package` | `--root`, `--manifest` defaults to `MANIFEST.json` | Complete file inventory and SHA-256 comparison against the supplied manifest. Does not authenticate the publisher or prove behavior. |

Use `verify-package` only when the project's existing release process supplies a manifest; its paths are relative to the supplied root. Contour does not require or ship a package manifest.

## Snapshot and scope

Without `--path`, the root of a Git repository is required: the selection includes tracked and untracked files visible to Git, excluding ignored files. For material ignored/generated files, specify an explicit selection containing **all** required inputs; explicit selection replaces git-visible selection rather than adding to it. Both policies declare the reserved root `.contour/` exclusion in `selection.excluded_subtrees`, which participates in the digest. Explicit product selections there are rejected. An ordinary nested product directory such as `src/.contour/` is not this reserved subtree. Keep product inputs outside root `.contour/`.

Traversal also excludes `.git` metadata, including a worktree's `.git` file. Symlinks are not followed; a symlink at root `.contour` is rejected. Submodules require a suitable separate selection or check. A missing explicitly selected path is reported in `absent_explicit_paths`; zero selected files do not establish sufficiency.

The snapshot includes only selected file bytes and the executable bit. Check the Git index and staging, service data, secrets and environment variables, actual installed tool versions, and database state separately. This report cannot restore the user's index state. For complete recovery, preserve relevant data in existing Git, CI, and working records. Writes between reads can cause a race: a single writer or real isolation is required.

Limits: JSON input up to 16 MiB, up to 20,000 files, each file up to 64 MiB, observed bytes up to 256 MiB, time-bounded file passes, and a 15-second limit per Git invocation. These protect the operation; they are not product-size thresholds. When a limit is exceeded, narrow the scope on a **justified** basis or use a prepared project tool; do not describe truncated observation as complete. A symlink, inaccessibility, or a change during reading produces an error or `INCOMPLETE`.

`scope` requires matching `project_id` values and selection policies. `src/` means a directory and its descendants; `src/file.py` means the exact path; `.` explicitly means the entire selection. Globs and shell substitutions are not supported. Pre-existing changes that are identical in before/after are not attributed to the new change. A protected-path change requires substantive review; it does not automatically prohibit correcting an erroneous test.

## Evidence and gate

When the project chooses this gate, supply its actual task record in the format shown in [task.example.json](../assets/task.example.json). Use the project's criteria and authority. Schema 1 fields are `id`, `project_id`, `revision`, `state`, `goal`, `endpoint`, `authorization_ref`, `acceptor`, `environment_id`, and `criteria`. Additional contract fields are also included in the digest. The digest excludes only `state`, `progress`, and `next_step`.

Before verification, call `gate --task ... --snapshot ...` without evidence. It returns `expected_binding` and an empty envelope. Preserve the binding with the actual primary results. Never attach an old PASS to a newly obtained digest. After verification, reconcile the actual candidate and relevant environment; if an input changed, rerun the affected check.

Each evidence record contains `id`, `criterion_id`, `criterion_revision`, `candidate_digest`, `contract_digest`, `environment_id`, `status`, `method`, `completed`, `primary_ref`, and `observed_at`. Method `test` with PASS requires a positive integer `selection_count`; this detects an empty selection but does not prove a sufficient test set. Preserve events and logs in the established system and reference them from the envelope. This tool does not verify timestamps or references over the network.

One envelope applies to the current candidate, contract revision, and environment. Preserve historical records for other candidates separately in the primary history; do not mix them into the current envelope or delete them to obtain a green status. Keep all attempts on the same current basis, including failed attempts, in the current set. After a substantive correction, the new candidate receives its own binding and new verification; it does not inherit an old PASS.

For a current criterion, all supplied applicable records must be successful and consistent. A red or partial attempt on the same candidate cannot be hidden by a green retry; it requires a substantive reviewer decision. The mechanical operation cannot distinguish an infrastructure flake from a product defect by itself. An authorized waiver contains `criterion_id`, `approved_by`, `authorization_ref`, `reason`, `compensation`, `expires_at` with a timezone, `task_revision`, `candidate_digest`, and `contract_digest`. The criterion must permit waivers in advance and list authorized approvers in `waiver_authorities`. Expired or unauthorized waivers are rejected. The failed check remains in `observations`.

`PASS` means consistency of the supplied data. A user can falsify a file, claimed role, `environment_id`, or reference; the operation does not provide cryptographic authentication. An independent acceptor must verify the actual criteria, primary events, environment applicability, and completion target reached. Where technical protection of controls is required, the selected platform provides it; these JSON fields do not.

## Resume and effects

`handoff` transfers facts without distorting them through summary. Effect statuses are `planned`, `sent`, `succeeded`, `failed`, `partial`, and `unknown`. Every effect has `reconcile_next`; a completed effect records a verified basis for requiring no next action. For a partial result, preserve per-step outcomes in the record rather than replacing everything with one `failed`. Before a new effect, persist intent, material parameters, and identity in a durable source, or rely on a verified equivalent platform log.

All eight operations only read inputs and emit a report. They may be repeated within the same authority, but each new report describes a new observation. An interrupted process may not emit complete stdout; do not accept truncated JSON. That error does not authorize deletion of a partial product or repetition of an external operation. Cancellation and retry of a project command follow that command's own contract.

## eval-summary input formats

Choose schema 1 for already complete trajectory totals, or schema 2 when individual attempts, missing observations or variant pairing must remain visible. Both return `ANALYSIS_ONLY`; successful parsing is not product acceptance or a competitive result. Preserve the primary observations separately. The operation reads the supplied JSON, not its source references, and does not authenticate any claim in it.

### Schema 1: complete supplied totals

Use the synthetic [evaluation.example.json](../assets/evaluation.example.json). From a standalone skill's root:

```bash
python3 -B scripts/contour_ops.py eval-summary --file assets/evaluation.example.json
```

From the extracted release archive's root:

```bash
python3 -B skills/contour-development/scripts/contour_ops.py eval-summary --file skills/contour-development/assets/evaluation.example.json
```

The object contains integer `schema_version: 1`, a nonempty `currency`, and a nonempty `units` array. Each unit has `id`, `variant`, Boolean `accepted`, positive integer `attempts`, nonnegative integer `total_tokens`, and finite nonnegative `total_cost`, `human_hours`, and `elapsed_seconds`. Each variant/id pair is unique. Include complete trajectories and all their attempts, agents, and attributable expenses; do not count dependent tickets as independent units.

Money, time, and budget values are supported within Python's finite float range; an excessively large integer or aggregate overflow returns a structured `ERROR`. Monetary totals here are descriptive statistics, not decimal-precision accounting. This limitation also applies to `handoff.budget.spent/limit`; integer counters are checked separately.

The example has two trajectories, one accepted, a total cost of 5, and a cost per accepted outcome of 5. This demonstrates arithmetic, not a measurement of Contour. With no accepted outcomes, `cost_per_accepted` is JSON null. The sum of trajectory times is not the wall-clock duration of an entire parallel campaign. The existing schema 1 contract and output shape are unchanged.

### Schema 2: partial observations and declared pairing

The [partial evaluation example](../assets/evaluation-partial.example.json) is synthetic. Its `.contour/evaluation/synthetic/` references illustrate project-local record locations, not existing files or measured runs. Use the same `eval-summary --file` command with this file. Replace every illustrative identity, source and measurement before using it for a real study.

Every field below is required; unknown fields are rejected. Use JSON `null` for an unknown permitted value, never an invented zero. A measured zero is a number with an observed basis.

| Object | Fields and meaning |
| --- | --- |
| Document | Integer `schema_version: 2`; nonempty `currency`; unique nonempty `variants` array; nonempty `units` array |
| Unit | `id`, `variant`, `case_id`, `repeat_id`, `criteria_ref`, `source_ref`; `accepted` as true, false or null; `expected_attempt_ids` as a nonempty unique ID array or null; `attempts` array; `overhead_cost`, `human_hours`, `elapsed_seconds` as finite nonnegative numbers or null |
| Attempt | Globally unique `id` and `source_ref`; `outcome` as `completed`, `failed`, `cancelled`, `incomplete` or `unknown`; `total_tokens` as a nonnegative integer or null; `total_cost` and `elapsed_seconds` as finite nonnegative numbers or null |

A unit is one whole trajectory, not each dependent subtask. A variant/id and a variant/case/repeat combination must be unique. Unit `source_ref` identifies the basis for acceptance, attempt inventory, overhead and whole-trajectory time; multiple units may cite a shared campaign record, but use unambiguous record locations. An attempt source must identify its distinct observed record; two entries cannot count the same attempt or reuse the same attempt source reference. Strings are nonempty and at most 4,096 characters. The pairing budget is 20,000 units times variants; expected and present attempt IDs are each limited to 20,000, within the general input size limit.

Include every attempt and agent, including failed and cancelled work. `expected_attempt_ids: null` means the inventory is unknown, not empty. `attempts` may be empty when expected observations are missing. Missing expected IDs, unexpected present IDs, unfinished outcomes and unknown inventories remain visible. An expected attempt cannot belong to two units or appear under another unit. The operation cannot discover attempts omitted from both the declared inventory and the observations.

Each attempt supplies one recorded, non-overlapping total for tokens and cost. Do not add cached-token counters to a provider total that already includes them. Allocate attributable non-attempt expense to `overhead_cost` once. Human hours are separate and are never silently priced. Unit `elapsed_seconds` is observed whole-trajectory wall time; attempt `elapsed_seconds` is that attempt's duration. Neither sum is the wall-clock duration of a parallel campaign.

Each variant reports `accepted`, `not_accepted`, `acceptance_unknown`, the attempt inventory, and five metrics: `total_tokens`, `total_cost`, `human_hours`, `elapsed_seconds` (sum of unit wall times), and `elapsed_seconds_sum` (sum of attempt durations). Each metric has `known_subtotal`, measured and unknown observation counts, `coverage_complete`, and `total`. A subtotal is retained even when a total is unknown. Attempt-derived totals require complete declared inventory and values; whole-trajectory time and human hours may be complete independently. Observation counts describe supplied or identified missing values, not proof that every real call was captured.

`cost_per_accepted` is null when cost coverage is incomplete, acceptance is unknown, or no unit was accepted. Failed work remains in cost. Pairing uses `case_id` and `repeat_id` across every declared variant; absent variants and differing `criteria_ref` values are reported without selecting a convenient matched subset. Equal references do not establish equivalent quality or independent acceptance.

The output always states `data_assurance: CALLER_SUPPLIED_NOT_AUTHENTICATED` and provides `no_claim_reasons`. A complete report still supplies descriptive arithmetic only. Provider telemetry acquisition, source authentication, quality adjudication, experimental independence, price validation and statistical comparison require actual external evidence under methodology section 19. This operation never invents them.

## Changing the catalog

Review operation changes against their contract and relevant normal and failure cases. Correcting a control requires an independent basis; changing expected behavior or evidence solely to pass is prohibited. An installed skill update does not update a separately saved archive. Prepared plans are fixed typed operation compositions; generated Code Mode requires applicable authorization for new auxiliary logic.
