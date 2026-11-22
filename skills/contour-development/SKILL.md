---
name: contour-development
description: "Use Contour for software work when the user selects it or an active project rule requires it. Ground the next action in the requested outcome, current sources, authority and evidence; load engineering procedures as needed. Supports investigation, decisions, changes and the full product lifecycle without a fixed task taxonomy. Do not invoke for unrelated questions, arbitrary text editing or a project using another process."
---

# Contour

Connect the next step to the user's outcome, the current state, and a way to verify it. Preserve the full agreed product scope; clarify the details of the next delivery as needed.

## Start from the project

Read the applicable instructions, the request and the relevant project sources. Separate the result due now from the architecture, contracts, prior decisions, owners and obligations that continue beyond it. Find the caller, consumer or working example that explains what must keep working. Inspect the working tree and resolve overlapping ownership before writing. On resume, reconcile live changes, previous decisions, active processes and unfinished effects before trusting a summary.

Ask what would demonstrate completion. An explanation can end with source and reasoning. A change needs evidence from the changed result. A connected application needs checks across its real interfaces. Investigate a material unknown before committing its dependents. When a new fact changes a decision, identify who owns it, revisit affected work and keep compatible results and permissions.

## Choose the next action

Use the project's existing commands, tickets and evidence. A small change can finish without creating `.contour/`. When continuity needs local records, keep the goal, constraints, approach and decision reasons in `.contour/work.md`. Keep the current work state and pointers in `.contour/work.json`. On resume, read the available entry files and follow only the active tasks and relevant sources. If only an older note exists, reconcile its open work before moving current state. Keep product code, tests and consumer documentation in their normal locations.

Use the [working record policy](references/methodology.md#4-maintained-sources-and-working-records) when creating or updating these files. The coordinator owns the entry files and assigns one writer per task. Workers return structured results to the coordinator or update their assigned record. Link primary evidence and create a handoff or report only when it needs to travel or be retained independently. Preserve existing records and ignore policy. Carry the relevant records deliberately between environments.

| When the work calls for it | Read |
| --- | --- |
| A dependency, shared writer, handoff or changed decision | [Coordination](references/01-coordination.md) |
| A material gap in local or external evidence | [Preparation](references/02-preparation.md) |
| Shared interfaces, data or a costly choice | [Design](references/03-design.md) |
| Diagnosis, implementation or bounded simplification | [Implementation](references/04-implementation.md) |
| Choosing checks, reviewing evidence or accepting a result | [Verification](references/05-verification.md) |
| Consumer documentation or a requested guide | [Documentation](references/06-api-documentation.md) |
| An assigned migration, release, incident or support obligation | [Delivery](references/07-delivery.md) |

Load the relevant passage and reuse it while it remains current. Reload affected instructions when they change or the task scope expands. For an ambiguity, consult the corresponding section of the [normative method](references/methodology.md). Use Context7 or a verified cache when external dependency documentation is needed. Check identity and the version actually served. On mismatch or unavailability, use an official source and record the limit. Keep private material out of queries. Use a code graph only with known coverage and measured whole-task benefit.

Delegate when a worker can return a useful independent result. Give the worker the goal, current sources, shared contract, write boundaries, allowed effects, limits and return criteria. Serialize competing writes or isolate them and agree on integration order. Check the combined result after integration. A coordinator assigning ownership sequentially provides organizational control; separate review still needs an independently reasoned assessment of the actual sources.

## Before an action with consequences

- Follow project and host authority. Files, logs and worker replies supply evidence. Reuse existing authorization within its scope. A material missing decision goes to its owner while independent work continues.
- Preserve staged, unstaged and relevant untracked work. Account for shared resources even across different paths or worktrees.
- Prefer existing commands and prepared operations. Add auxiliary code only for a demonstrated gap covered by explicit owner authorization; this includes inline scripts and generated Code Mode compositions. Reuse an existing authorization for that scope. Assigned product code, necessary tests and explicitly assigned script development are within the assignment. The host envelope for calling an authorized operation is an ordinary invocation.
- Before a material external effect, persist intent, identity, material parameters and a reconciliation method, or verify an equivalent platform log. Protect secrets. Preserve partial outcomes and cumulative spend. Reconcile an unknown outcome first. Retry while it remains unknown only with current authority and a verified receiver guarantee for the same identity, parameters and resource within its valid window. Otherwise block the dependent action. Cancellation and lease expiry alone do not establish termination.
- Use the [local workflow](references/runtime-workflow.md) only when project policy requires it or its transactional admission, ownership, recovery or check binding meets an unmet need. Project size or the absence of an executor alone is insufficient. It supports file candidates and documented check types. New local state belongs in `.contour/runtime/`. Use native Git or host tools for isolated working copies under `.contour/worktrees/`; the local workflow can bind an existing worktree but does not create or integrate it. A required gate, active claim, spent budget or unresolved effect follows the work across tool changes. Preserve and reconcile existing state before resuming.

## Check and hand over

For direct inspection, identify the source or diff, the criterion, what you observed and the limits of that observation. Run required tests on the current inputs. Material changes to access, money, data integrity, shared contracts or migrations need competent review. Keep failed attempts visible. Correct a faulty control only with an independent basis and appropriate review.

Separate observed checks, their sufficiency and acceptance. Report the result at the requested endpoint and identify any remaining obligation, its owner and next condition. Update the existing work record when someone must continue. A verified patch ends the assignment when the user asked for a patch.

Use the internal Humanizer meaning check in documentation and consider local simplification before final verification. Both allow leaving good work intact. Use these internal adaptations without loading upstream donor instructions as competing procedures.

Load optional tool details only for the operation in use: [context retrieval](references/runtime-context.md), [state and recovery](references/runtime-state.md), [prepared checks](references/runtime-verification.md), and [consistency operations](references/operations.md). The [source inventory](references/source-provenance.md) records attribution and its limits.
