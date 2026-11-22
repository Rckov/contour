# Durable task state

When the local adapter is selected, use its prepared workflow entry point. Read
this reference when integrating that controller, reconciling its interrupted
work, or diagnosing its state conflicts. The implementation is
`../scripts/contour_state.py`.

## Contents

- [Responsibility and boundaries](#responsibility-and-boundaries)
- [Public interface](#public-interface)
- [Task and revision operations](#task-and-revision-operations)
- [Ownership and attempts](#ownership-and-attempts)
- [Verification and acceptance](#verification-and-acceptance)
- [Material effects and recovery](#material-effects-and-recovery)
- [Profile changes and backup](#profile-changes-and-backup)
- [Failure checks](#failure-checks)

## Responsibility and boundaries

The controller executes local state rules. It stores current task revisions,
dependency pins, exclusive resource claims, durable attempts, effect intent,
receipts, and an event history in SQLite. It does not invoke a project command,
agent, shell, deployment, or network service. An executor performs a prepared
operation after `begin-attempt` commits and then calls `finish-attempt`.

Use one database on the supported local filesystem, with `DELETE` journal mode,
`FULL` synchronization, foreign keys, and short `BEGIN IMMEDIATE` transactions.
The module bounds lock waits to two seconds. Never hold a transaction while
waiting for an agent or external system. Unsupported schema versions fail
explicitly; they are not reinterpreted or reset.

This is not a security boundary against a process with the same permissions.
Generation tokens fence controller calls, not arbitrary filesystem writes.
`LOCAL_OBSERVED` identifies how a prepared executor obtained a result;
`HOST_OBSERVED` identifies an observation from the agent host. Neither is a
protected signature. Production authorization and trusted CI must use the
project's actual controls. A task acceptor is recorded as `LOCAL_DECLARED`.

The project profile binds prepared command policy. The workflow adapter computes
and checks its real digest and validates explicitly assigned existing worktrees.
Native Git or host tools create and integrate those working copies. The controller
compares identities; it cannot infer that an arbitrary digest supplied by a
caller describes the live filesystem.

## Public interface

```python
from pathlib import Path
from contour_state import Controller, StateError

controller = Controller(Path("/absolute/project/.contour/runtime/state.sqlite3"))
controller.initialize(project_id, project_root, profile_digest)
result = controller.apply(request)
current = controller.status(task_id)
```

Create the state parent directory before low-level initialization; the workflow
entry point prepares the standard `.contour/runtime/` layout. State and logs stay
under the reserved `.contour/` subtree, outside product snapshots. Storage
ancestors must be real directories, not symlinks. Existing initialization succeeds
only when project identity, canonical root, and profile digest agree.

Every request needs `op` and a unique `request_id`. Repeating the identical
request returns its saved result with `replayed: true`; reusing the id with
different content fails with `CONFLICT`. Generate a new id for a genuinely new
decision, not to bypass a failed precondition.

Task mutations other than registration require `task_id`, `expected_revision`,
and `expected_seq`. Read current state before proposing a mutation. A successful
mutation advances sequence once; a changed dependency can also advance it.
Conflicting requests do not overwrite newer state. A replay returns the original
result, which may now be historical; read `status` for current state.

Success contains `status: OK`, `op`, the updated `task`, and the applicable
`claim`, `attempt`, or `effect`. `begin-attempt` additionally returns
`attempt_id` and `binding`. `status(task_id)` contains `project`, `task`, `claims`,
`attempts`, `effects`, and `events`; `status()` lists project tasks.

Errors raise `StateError` with `code`, `message`, `details`, and `as_dict()`.
Important codes include `INVALID`, `CONFLICT`, `BLOCKED`, `STALE`, `FENCED`,
`BUDGET`, `EVIDENCE`, `AUTHORITY`, `PROFILE`, `SCHEMA`, `BUSY`, and `STORAGE`.
Do not turn an error into an invented success record. Resolve its cause using
observed state. Requests and individual JSON records are bounded to 4 MiB.

## Task and revision operations

`register` requires:

- `task_id`, `goal`, `endpoint`, `authority_ref`, and `acceptor`;
- SHA-256 `candidate_digest` and `profile_digest`;
- nonempty `criteria`, each with `id`, `description`, `kind`, and nonempty
  `operation_ids`; kinds are `test`, `process`, and `review`;
- `budget: {max_attempts, max_seconds}`, both positive;
- optional `dependencies: [{task_id, revision}]` and `workspace_root`.

The workspace defaults to the initialized project root. A supplied workspace
must be an existing absolute directory. The workflow adapter verifies that it is
the project root or an actual Git worktree sharing that repository's common Git
directory. Create working copies through native Git or host tools before
assignment; a directory name is not proof of repository identity.

Registration creates revision 1, sequence 1, state `clarifying`. States are
`clarifying`, `ready`, `running`, `proposed`, `accepted`, `blocked`, `paused`, and
`closed`. Endpoint values are `analysis`, `demo`, `patch`, `integrated`,
`release-ready`, `deployed`, and `observed`. These are this adapter's fields,
not a mandatory classification step for all work using Contour.

`transition` requires `to` and `reason`. Entering `ready`, `running`, `proposed`,
or `accepted` also requires matching current candidate/profile digests and
current accepted hard dependencies. `running` requires a live claim. Moving
from `paused` or `blocked` to active work requires `resume_ref` and reconciled
attempts. Moving into `proposed`, `accepted`, `closed`, or back to `clarifying`
cannot discard an incomplete attempt.

`update-candidate` requires a new `candidate_digest` and `reason`. It preserves
product revision and cumulative spend while invalidating old candidate evidence.
It may also assign an existing `workspace_root`, with `assignment_ref` identifying
the observed native worktree or integration result. It cannot discard pending attempts or
change an accepted/closed revision. The operation does not clear stale
dependencies. A changed candidate from `proposed` returns to `running`.
When an owner exists, the same transaction moves its automatic workspace
reservation to the new root while preserving its generation, access, explicit
resources, and spend. An incompatible occupied destination rejects the entire
assignment. After a successful move, the old root is available to another task.

`revise` requires `reason` and `candidate_digest`. It may replace goal, endpoint,
criteria, dependency pins, acceptor, authority reference, or profile digest.
It preserves the existing budget; a supplied `budget` is rejected rather than
silently ignored. Use `adjust-budget` for a resource allocation decision.
Release observed stopped workers and reconcile unfinished effects first. A new
revision starts in `clarifying`; previous acceptance remains historical. The
new profile must match the project's current profile. Known failed effects from
an earlier revision remain historical; unresolved effects are never erased.

`adjust-budget` changes total ceilings without resetting expenditure, attempts,
reservations, candidate identity, criteria, ownership, or revision. Supply the
normal expected revision/sequence, a complete positive `budget`, `reason`,
`authorized_by` matching the task's declared `acceptor`, the task's current
`authority_ref`, and `decision_ref` identifying the owner's actual allocation
decision or applicable prior delegation. The local profile assigns this
resource decision to its declared acceptor; an execution agent cannot grant
itself more resources. Existing authorization can cover the change; do not ask
again when it does. These fields are `LOCAL_DECLARED`, not authentication or
proof that the referenced decision exists. The host/owner verifies that basis.

The controller records old and new ceilings and the consumed/reserved amounts
in its durable event. A new ceiling cannot exclude spent attempts or consumed
and reserved time. An unchanged budget, stale sequence, mismatched authority,
missing decision reference, or terminal revision is rejected. A pending attempt
keeps its original identity and reservation; changing the budget does not
reconcile it. A repeated identical request returns its original result. New
work on terminal revisions uses `revise` first and retains cumulative spend.

Dependencies are pinned to a task revision. Cycles are rejected. A revised
dependency marks affected descendants stale, preserves their evidence, and
leaves unrelated tasks runnable. Reassess and revise affected dependency pins;
changing a source digest alone cannot make them current again.

## Ownership and attempts

`claim` requires `worker_id`, unique explicit `resources`, and `lease_seconds`
between zero and one day, excluding zero. It returns a monotonic `generation`.
Optional `access` is `write` by default or `read`. The controller always adds
`workspace:<canonical absolute root>`; callers cannot supply that reserved
namespace. Equal, ancestor and descendant product workspace paths overlap.
The project's reserved `.contour/worktrees/` descendants are independent of its
main product workspace; equal or nested worktree paths still conflict. The workflow
verifies actual existing Git worktree identity before assignment. Different explicit
resource names cannot evade ownership. One unreleased owner can hold each task.

Read claims may share overlapping workspaces only when no write owner holds one; a writer
conflicts with every existing reader or writer. A pure read claim may use
`resources: []`. Explicit named resources remain exclusive, including between
readers. Choose canonical keys for shared target branches, schemas, lockfiles,
databases, API contracts, and deployment environments. These remain distinct from
the automatically reserved workspace; different names for the same external
resource do not make it independent.

Read access is a coordination declaration, not filesystem permission enforcement.
Use it only for genuinely read-only work and dispatch it with the actual host's
appropriate restrictions. Separate worktrees and host permissions remain needed
for raw writes. The controller does not protect against hostile directory swaps,
raw filesystem access, or an undeclared overlapping external resource.

`heartbeat` requires worker, generation, and lease duration. An expired lease
additionally requires `observation_ref`. Expiration never frees a resource.
`release` requires worker, generation, and `stopped_ref`; pending or unknown
attempts block release. A cancellation request is not a stopped observation.
Keep an uncertain old worktree quarantined. Host restrictions must prevent raw
cross-worktree writes when such enforcement is required.

`begin-attempt` requires:

- current worker/generation, `operation_id`, and current candidate/profile;
- `category`: `check` (default), `worker`, or `integration`;
- `criterion_ids`; checks require at least one current criterion and an allowed
  operation mapping, while other categories may use an empty list;
- positive `reserve_seconds`, including command, observation, and cleanup
  allowance; optional `effect_id` for an already planned effect.

It requires `running`, current dependencies, a live claim, no incomplete attempt,
and remaining aggregate budget. It commits an attempt id, binding, and budget
reservation before returning. Pass the returned binding unchanged to the
executor. It includes project/task/revision, workspace, candidate/profile,
criterion ids, worker/generation, operation, and attempt identity. Effect attempts
also bind immutable effect identity and digest.

Workspace claims apply to existing assigned working copies. The controller does
not create Git worktrees, integrate commits or reserve a destination before a
native Git operation. The coordinator controls creation and observes its result,
then assigns the existing workspace before dispatching a writer. Reassignment
moves the current workspace reservation while preserving generation, explicit
resources and expenditure.

`finish-attempt` requires attempt id, worker/generation, and actual receipt.
Matching duplicate receipts are idempotent; conflicting terminal results fail.
A later observed completion after an unknown/stopped observation requires
`resolution_ref`, the same still-current owner and binding, and no reduction in
already observed execution time. Prior observations remain in the event history.
A released or superseded owner cannot use a late result to reclaim authority.

Only `finish-attempt` may cross an older `expected_seq` when every intervening
durable event is a heartbeat from the exact current worker, generation and
revision. The complete event suffix and receipt settlement are checked in one
transaction. Pause, budget, dependency, termination, profile and other control
changes remain conflicts. This exception permits lease renewal during actual
work; it does not weaken receipt currency or ownership checks.

Every attempt consumes its count before invocation. Completed receipts settle
time using observed duration, including measured overrun. Unknown attempts keep
their reservation. Recovery without a complete result charges at least the
reserved duration and any greater observed duration. `seconds_used` therefore
includes conservative recovery charges; use actual receipts for measured timing.
Revisions, new agents, sessions, and profile updates never reset these counters.
A failed same-operation attempt on the same basis requires `resolution_ref`
before retry. The basis includes candidate, profile, and workspace identity. A
repaired candidate or reassigned workspace may be checked on its new basis;
unchanged bytes in another directory do not make old evidence current.

## Verification and acceptance

The prepared verifier supplies `LOCAL_OBSERVED` receipts with schema version 1,
binding, kind, completed/status, candidate-before/after digests, duration,
returncode, argv, cwd, bounded log references/digests/sizes, and test counts when
applicable. Diagnostic errors before launch may have empty argv/logs and missing
candidate observations. Store them faithfully; they cannot satisfy criteria.

For a `test` criterion, require a current `check` receipt of kind `unittest` or a
qualified `junit` producer. It must be completed `PASS`, return zero, preserve the
pinned candidate, select and successfully execute at least one test, have no
failures/errors/unexpected successes, and report internally consistent counts.
All-skipped, empty, failed, changed-candidate, and incomplete runs cannot pass.

A `process` criterion needs an explicitly mapped process check. It does not
prove test behavior simply because the process returned zero. A `review`
criterion needs a bound `HOST_OBSERVED` review receipt and `witness_ref`.
Worker/integration observations use their own categories and witness references;
they never count as successful verification.

`transition` to `accepted` requires the declared `acceptor` and `authority_ref`,
plus `evidence: {criterion_id: attempt_id}` for every current criterion exactly.
The controller checks bindings, workspace identity, category, outcome, and current dependencies. It
blocks unfinished current material effects and any unresolved historical effect.
The selected evidence must be the latest check admitted for that criterion on
the current revision, candidate, workspace and profile. A later failed, errored,
incomplete or stopped check supersedes an older PASS. A fresh check after such
evidence requires a substantive `resolution_ref`, including when another
allowed operation checks the same criterion. A successful current rerun can
satisfy the criterion; the original result, stop observation and spend remain.
Reconciliation of process termination does not turn its check into a success.
It does not assess whether the chosen criteria are semantically sufficient for
the requested product. The acceptance owner must assess that separately under
the methodology's verification procedure.

## Material effects and recovery

`plan-effect` requires the current worker/generation and immutable `effect_id`,
`kind`, `target`, `environment`, `parameters`, `authority_ref`, `expected_result`,
and `reconcile_method`. Keep credentials in protected references, not parameters
or logs. Optional receiver idempotency data contains `key`, `verified_ref`,
`expires_at`, and `scope`.

Starting its attempt changes the effect to `unknown` before possible dispatch.
This deliberately covers a controller crash before or after the real send. A
successful generic process result cannot establish receiver success. The module
does not dispatch remote operations; the actual authorized adapter does.

`reconcile-effect` records `effect_id`, `outcome`, `observation_ref`, and `details`.
Outcomes are `succeeded`, `failed`, `partial`, `unknown`, or `not_sent`. A receiving
system observation determines the outcome; no response is not proof of failure.
Observed success cannot be erased. Compensation is a separate authorized effect.
For a planned operation that was never dispatched, record the host/receiver
observation as `not_sent` before revising an authorized goal that no longer needs
it. For a known failed operation, complete the remaining authorized work or revise
the commitment explicitly. Keep its old effect record and observation; do not
delete them to unblock a revision. Unknown or partial work still needs observation
and cannot be relabeled merely to make a transition pass.

Retry requires the exact saved reconciliation reference as `resolution_ref`.
Unknown/partial outcomes additionally require a verified unexpired receiver
deduplication contract and a matching `retry_authority_ref`. Preserve the same
effect id and parameters. A fresh client key or an HTTP verb is insufficient.
An observed successful effect cannot be repeated through the same identity.

`resume` receives `observations` mapping incomplete attempt ids to
`{outcome: stopped|running|unknown, observation_ref}`. It never restarts an
operation or creates passing evidence. A stopped worker observation settles the
attempt conservatively, while its external effect remains unknown until the
receiver is reconciled. Observe descendants and shared services, not just a
reused numeric PID.

## Profile changes and backup

`reconfigure` is project-level and requires `expected_profile_digest`,
`new_profile_digest`, `reason`, and `authority_ref`. It refuses live claims,
incomplete attempts, and unfinished effects. It changes the profile atomically,
marks all task bases stale, and preserves histories and budgets. Revise affected
tasks onto the new profile before running them. The workflow adapter must retain
its own durable intent and reconcile any mismatch between the profile file and
database; replacing a file alone cannot update task evidence.

`backup` requires a fresh absolute `destination`; place new backups under
`.contour/`, for example `.contour/runtime/backups/<unique-id>.sqlite3`. It uses SQLite's backup API,
checks the result, and never overwrites an existing file. The backup represents a
consistent snapshot before its own backup event. A crash after copying but before
recording completion may leave the fresh backup file; inspect it and choose a new
destination rather than overwriting it blindly. Back up current project evidence
and policy files separately; the database contains references, not every artifact.

No automatic schema migration is provided for an unknown schema. Retain the
matching prior controller or perform an explicitly implemented, tested migration
to a new database and verify histories, budgets, effects, and current identities.
Never delete state to resolve a profile or schema conflict.

## Failure checks

When changing state behavior, check affected ownership, stale-state, recovery,
evidence and budget contracts, including concurrent or interrupted operations.
Preserve existing state while exercising failure cases. Local process checks do
not establish hardware power-loss guarantees or qualify an unexecuted remote adapter.

