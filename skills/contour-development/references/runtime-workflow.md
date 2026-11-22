# Executable local workflow

Use this local adapter when project policy requires it or its transactional admission, ownership, recovery or check bindings address an actual unmet need. A missing equivalent executor alone is not a reason to initialize one. Existing project tools, records and sequential coordination may already be sufficient; the method also permits source-backed answers and direct inspection without this adapter. Switching tools must not evade applicable gates, live ownership, spent budgets or unresolved effects.

This adapter accepts file candidates backed by its documented test, build/lint or host-observed review criteria. It does not manufacture receipts for direct inspection, nor require an investigation to create a product file. Choose another supported project mechanism for those outcomes before execution, unless an existing policy requires this adapter; resolve such a policy conflict with its owner without declaring the unmet gate satisfied.

The host supplies the model and native agent tools. Python 3.12 on Linux supplies local state and checks. Core operations require no pip dependencies. Git supplies read-only repository/worktree observations; worktree creation and integration use native Git or host tools. A supported Node/JUnit producer is optional. The setup below applies only to this selected adapter.

## Contents

- [Setup](#setup)
- [One task through execution](#one-task-through-execution)
- [Native subagents and review](#native-subagents-and-review)
- [Worktrees and integration](#worktrees-and-integration)
- [Pause, recovery and external effects](#pause-recovery-and-external-effects)
- [Update and recovery of the toolkit](#update-and-recovery-of-the-toolkit)
- [Output and trust](#output-and-trust)

## Setup

Read the actual project instructions and available commands. `contour_context.py --root /absolute/project inspect` reports facts and instruction paths; it neither interprets instruction precedence for the host nor establishes Context7 connectivity. Discover the host's Context7 resolve/query capabilities and exercise them when external versioned documentation is needed. Record the exact package, requested and served versions, timestamp and primary reference; use the documented official fallback for a failed or mismatched query. `not-needed-yet` does not claim a connected service.

Fill the [execution profile](../assets/execution-profile.example.json) with project facts and an existing review/authority reference. Checks are exact reviewed entries. `unittest`, the narrow `junit-leaf-v1` producer profile, and `process` build/lint checks have separate contracts in [runtime-verification.md](runtime-verification.md). A review-only task can use an empty check catalog. Existing product commands are preferable to new auxiliary programs. A profile containing placeholder values is not a project decision.

All checks in this local profile use one complete source selection. Default `.` includes material inputs, including ignored files, with the reserved root `.contour/` subtree explicitly excluded and bound into the snapshot digest. Keep runtime state, reports and generated control output there, and keep product inputs elsewhere. Other ignored inputs and nested product directories named `.contour` remain in scope. For a large project, justify an explicit complete selection rather than silently dropping inputs. External services and dependency/environment state need their own recorded checks.

Prepare the project-specific profile at `.contour/execution-profile.json`. From the installed skill root:

```bash
python3 -B scripts/contour_workflow.py init --root /absolute/project --profile /absolute/project/.contour/execution-profile.json
python3 -B scripts/contour_workflow.py status --workspace /absolute/project/.contour/runtime
```

Omitting `--workspace` selects `.contour/runtime/`, containing `project.json`, `state.sqlite3`, `runs/` and `packets/`. Initialization creates `.contour/.gitignore` with `*` only when absent. It preserves existing records and ignore policy and does not edit root `AGENTS.md`, root `.gitignore` or product configuration. It creates no Git worktree or integration directory. Explicit existing external workspaces remain supported; preserve their history rather than creating a replacement ledger. New records use the project-local layout.

If interruption occurred before the first configuration write, an empty assigned runtime directory can be initialized; preserve an unrecognized nonempty directory for reconciliation. Repeating identical initialization validates durable identity, preserves the database and history, and completes missing runtime directories. A file or symlink in place of a required directory is a conflict, not something to overwrite. Root `.contour` and storage ancestors must be real directories. The profile and toolchain are hashed. A changed profile/toolchain requires explicit reconfiguration; do not edit `project.json` or the database to clear a gate.

## One task through execution

The JSON request contracts are in [runtime-state.md](runtime-state.md). Requests contain a unique `request_id`; task mutations include the expected revision and sequence returned by the preceding observed result. A conflict means reconcile current state; never invent a newer number without inspecting what changed.

For a new application, an observed empty project root is a valid starting basis for registration, `ready`, `running`, and the initial implementation worker packet. Admit ownership before writing the first product files; no placeholder seed file is needed. Missing explicitly selected paths and an incomplete snapshot still fail. Actual checks, `proposed`, and `accepted` retain the nonempty-candidate requirement. After initial implementation, `update-candidate` observes the real source and normal verification continues. An empty root or a dispatched worker is not an accepted application.

1. `request` with `register` records goal, endpoint, existing authority, acceptor, criteria and total attempt/time budget. The workflow observes the actual candidate and profile instead of asking the agent to compute their hashes. Each executable criterion maps to reviewed operation IDs. A small task needs one contract, not a document per state.
2. Move `clarifying` to `ready`; claim the actual resources with a worker identity and bounded lease; move to `running`. Claims also cover shared APIs, database/migration order and other semantic resources when applicable.
3. Implement within the assignment. For a changed candidate, `update-candidate` records its actual new digest and reason without resetting the intent revision or spend. Changing accepted intent uses `revise`, preserving history and invalidating dependent basis.
4. Run `check` or a finite `plan` using catalog IDs. The workflow commits the attempt and reserves budget before invocation, then persists the runner's actual receipt. A duplicate request does not execute again. A failed/unknown outcome stops the plan; it does not skip ahead or retry automatically.
5. Move to `proposed` with the stable candidate. The acceptor examines actual results and sufficiency. Transition to `accepted` maps every criterion to its current successful attempt, with the declared acceptor/authority. Verification and acceptance remain separate operations.

For each criterion, select the latest check admitted on the current revision, source, workspace and profile. A later failure or incomplete/stopped check prevents reuse of an earlier PASS. Reconcile the cause and run a current justified check, recording `resolution_ref`; changing to another allowed operation does not hide contradictory criterion evidence. The failed observation and resource spend remain in history. Stopping a process settles ownership, not verification.

`accepted` is already a terminal successful revision. Release its stopped writer's claim when appropriate, then report the result; do not transition it to `closed`. `closed` is the separate terminal outcome for work ended without acceptance. New work uses `revise` or a linked task. Administrative closure in an external issue tracker is not a controller state transition and needs that task's applicable authority.

```bash
python3 -B scripts/contour_workflow.py request --workspace /absolute/project/.contour/runtime --file /absolute/project/.contour/runtime/request.json
python3 -B scripts/contour_workflow.py check --workspace /absolute/project/.contour/runtime --file /absolute/project/.contour/runtime/check-request.json
```

The check request contains `request_id`, `task_id`, `expected_revision`, `expected_seq`, `worker_id`, `generation`, `check_id`, and `criterion_ids`. An explicitly reviewed same-basis retry also records `resolution_ref`; original failures remain. Actual reserve is the check timeout plus cleanup/observation allowance, so the total task budget must cover it. An overrun is recorded and blocks subsequent overspend.

A current owner's heartbeats may renew its lease while a prepared check runs. Receipt settlement accepts an older sequence only when the entire intervening event suffix contains that exact owner's heartbeats. Other control changes still conflict and require reconciliation; do not restart a completed process merely to obtain a new receipt.

When the owner changes a total allocation within actual authority, use the prepared `request` operation with `op: adjust-budget`, the new `budget`, `authorized_by`, `authority_ref`, `decision_ref`, and `reason`; see the [state contract](runtime-state.md). It preserves all consumed and reserved resources. `revise` does not reset or silently change the budget. Exhaustion alone is not permission to increase it, create a replacement ledger, or weaken a criterion.

A plan contains `task_id`, expected revision/sequence, worker identity/generation and `steps`. Each step contains only `request_id`, `check_id` and `criterion_ids`. It has no expressions, shell strings, loops, dynamic tool names or implicit retry. This is the prepared use of the Code Mode idea: execute the finite mechanics and return substantive results without generating a new program.

## Native subagents and review

`worker-packet` durably admits a worker attempt and writes an assignment JSON. Its request includes the current task/claim, `operation_id`, `reserve_seconds`, empty `criterion_ids`, and `assignment` with `goal`, `scope`, `inputs`, `output`, `stop_conditions`. It returns `dispatch: NOT_YET_PERFORMED`. The coordinator then calls the actual available native subagent tool, supplies the packet and relevant procedure paths, and records the returned host identity. A file or a claimed agent name is not a dispatch observation.

The packet carries the authority reference and acceptor from the admitted task revision. Project setup authority is not substituted for a narrower or newer task authorization. A packet still records declared authority; it does not grant permissions beyond the actual user/environment boundary. Recording a returned host result can observe an empty product root, so a failed initial worker's known completion and spend are preserved even when it created no files. This is result recording, not successful verification or acceptance.

`host-result` records the actual returned artifacts, completion, status, observed duration and host `witness_ref`, using the admitted attempt identity and current owner/revision/sequence. The returned `result.binding` must exactly match the packet; the wrapper never repairs a mismatched response by replacing its identity. A worker result cannot satisfy a test or review criterion. `review-packet` instead admits an actual review operation mapped by a `review` criterion; its completed native result can supply that criterion, subject to current binding and the acceptor's sufficiency decision. Reviewers are independent in assignment, not protected by a role label. Preserve a partial result and later completion as separate immutable observations; a late completion also requires the controller's `resolution_ref`.

Use actual host timing/status. Missing telemetry is unknown, not zero. A stale or late worker cannot change current task state merely by sending a result. Preserve its artifacts for reconciliation. Further delegation must fit the packet and actual host concurrency limits.

## Worktrees and integration

Use native Git, host worktree tools or the project's existing procedure when parallel writing needs separate working copies. Put new Contour working copies under `.contour/worktrees/`, for example `.contour/worktrees/worker-a`; create that directory only when needed. A single local edit need not create a worktree. The coordinator serializes creation, observes the actual result and resolves ownership before dispatching writers. Preserve staged, unstaged and relevant untracked work and follow the project's hooks and integration rules.

The workflow may use the project root or an explicitly assigned existing Git worktree of the same repository. For a new task, supply its absolute `workspace_root` to `register`. To move existing work, use `update-candidate` with `workspace_root`, `assignment_ref` identifying the observed native operation, and `reason`. The adapter checks the actual worktree root, common Git directory and native worktree registration; a directory name alone is insufficient. It observes the new candidate rather than transferring an old receipt to it. Pending attempts and incompatible ownership still block reassignment.

Claims cover the assigned workspace. Equal or nested product paths conflict whenever either owner writes; expiration does not release a claim. Worktrees below the reserved `.contour/worktrees/` are independent of the main product workspace, while equal or nested worktree paths still conflict. Native creation happens outside the controller: it does not reserve a future destination or wrap the Git mutation in a transaction. Coordinate that step explicitly instead of attributing a guarantee to the ledger.

Integrate identified changes through the project's normal Git or host procedure against the current baseline. Check the actual changes against the assigned scope, including renames and relevant uncommitted files; resolve conflicts without overwriting another owner's work. Keep the integration result at an assigned workspace, call `update-candidate` after material changes, then run affected interface checks and the required verification on that combined source. A clean merge or an old worktree receipt does not establish acceptance.

The workflow exposes no `worktree` or `integrate` command. Native operations retain their own permissions, hooks, failure behavior and recovery. If one fails or its result is uncertain, inspect the real Git/worktree state before retrying; Contour does not automatically roll it back or publish anything. Working copies do not isolate credentials, shared services or another process with filesystem access.

## Pause, recovery and external effects

`status` is read-only. `resume` records actual observations of pending attempts; it never restarts them automatically. A lease expiry, timeout or cancellation request does not prove a worker stopped. Reconcile actual processes, resource ownership and partial results before releasing a claim. A stopped attempt without a factual result cannot become PASS. The original intent/result gaps remain in history.

Keep the controller authoritative for its tasks, attempts and claims. A navigator can point to `runtime/project.json`; use `status` to read live state. For another environment, deliberately carry the relevant entry files and their referenced sources; Git does not transfer ignored runtime state. Transferring a controller also requires a consistent database and its referenced artifacts, followed by identity and environment reconciliation. A note alone does not recreate missing attempt history, permissions, spend or active processes.

For a material external action, `plan-effect` records stable identity, parameters, target, environment, authority and reconciliation procedure before the actual host tool call. A native worker attempt can bind that effect. The controller journals the decision; it does not implement arbitrary HTTP, deployment, payment or other external mutations. Use the project's real prepared tool within current authority. `reconcile-effect` records the receiver observation. A retry of a potentially completed effect requires the same immutable effect, a verified current receiver guarantee and current authority; otherwise the dependent action remains blocked.

## Update and recovery of the toolkit

Keep the prior package and state. Stop/reconcile workers and effects, release claims and use `backup` to create a fresh SQLite snapshot under `.contour/`. An unsupported database schema fails explicitly; this release does not reinterpret another schema.

`reconfigure` consumes a JSON object with `request_id`, the complete new `profile`, `reason`, and `authority_ref`. It persists the exact replacement intent, updates the controller profile and then the project file. If interrupted between those writes, submit the same request under the same toolchain; the retained intent completes the replacement without resetting spend. A conflicting request or changed toolchain is rejected for reconciliation. Reconfiguration invalidates affected task basis; revise and rerun applicable evidence. Restoring an old tool version does not roll back product changes or external effects.

## Output and trust

Successful workflow/state commands return JSON with status `OK`; checks return their actual outcome. Exit 0 means a completed operation result, exit 2 means a non-passing/reconciliation outcome, and exit 1 means invalid input, state conflict or an execution/storage error. An exception after durable admission leaves a pending attempt that requires reconciliation; never interpret a missing receipt as no action.

`check` and `plan` accept `--view compact` for readers that understand its envelope. Default output and `--view full` keep the existing JSON shape. The optional view replaces only an identical repeated receipt under `controller.attempt.receipt` with a reference to the retained receipt. It changes neither execution, exit status, durable history nor receipt contents. For example:

```bash
python3 -B scripts/contour_workflow.py check --workspace /absolute/project/.contour/runtime --file /absolute/project/.contour/runtime/check-request.json --view compact
```

The envelope has `view` and `result`. Read the outcome at `result.status`; an envelope is not a PASS. `view.name` is `contour-workflow-compact` and `view.schema_version` is 1. Each entry in `view.references` names a `from` slot and a `to` target as JSON Pointer fragments rooted at the envelope. Expand only those listed slots to reconstruct the full result. A `$ref`-looking object elsewhere remains ordinary data. Plan references point inside the corresponding `result.results` entry. Unequal receipt copies remain intact and produce `RECEIPT_COPY_MISMATCH` in `view.inconsistencies`; reconcile the discrepancy rather than choosing the favorable copy.

Compact output avoids repeated text when a duplicate exists. A replay without a duplicate retains all its data and gains envelope overhead, so compact is not universally smaller. Use it for agent consumption of ordinary check/plan receipts when the consumer supports references; retain full output for existing parsers. Argument and execution errors still use the ordinary error format; check the exit code and actual JSON shape. No provider-token or whole-task savings follows from serialized byte reduction alone.

`LOCAL_OBSERVED` and `HOST_OBSERVED` are provenance descriptions within a cooperative local session. The database, code, files and receipts are not isolated from an adversarial process with the same effective privileges. The host and protected CI must supply stronger enforcement when required. Rehashing an imported JSON report never upgrades its origin. Local acceptance is explicitly `LOCAL_DECLARED` and does not authorize an external release.
