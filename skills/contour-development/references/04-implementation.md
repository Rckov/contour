# Implementation, diagnosis, and simplification

## Contract

Input: the current task, authorized change, behavior to preserve, write scope, and verification. Output: a candidate, an explainable change, actual evidence, and remaining questions. Do not rework adjacent code outside the assignment.

## Implementation

Reconcile the basis and other people's changes. Make a sufficient, cohesive change. Do not format or revert unrelated work. Consider affected dimensions of behavior: type and shape, absence/null/empty values, error channel and timing, asynchronous behavior and cancellation, order and completeness, number of effects, reference identity, resources and concurrency, serialization, and existing consumers.

Use project commands. A new scanner, runner, or one-off program does not become a product test merely by being named one. An ordinary regression test checks a product criterion in the established framework and authorized environment. For new auxiliary code, identify the catalog gap, inputs, outputs, effects, benefit, and verification; reuse existing scoped authorization or request only what is missing.

## Diagnosis

1. Record the symptom, conditions, expected behavior, and behavior to preserve.
2. Distinguish a product defect from a runner, fixture, or environment failure and from an empty selection. A missing command is not a reproduced RED result.
3. State a causal hypothesis and an observation that distinguishes it from alternatives. Use current sources. When external documentation is needed, follow the preparation procedure for versioned documentation.
4. Perform a bounded, authorized check. Compare the baseline using relevant data, configuration, and the user's original changes. Do not reset the working tree; account for shared worktree resources.
5. Fix a sufficient cause and check the original case and affected regression behavior. Preserve observations from failed attempts. Do not repeat the same experiment under a new name; the number of failures alone does not prove an architectural defect.

During an incident, authorized containment may precede finding the cause; report these outcomes separately.

## Internal simplification

Apply the adapted code-simplification approach. Seek a concrete benefit: remove an unnecessary branch, clarify a condition, or reduce duplication while preserving the contract. Complexity that provides a required property is not waste. Keep the change within the current assignment.

Check preconditions: replacing an object with a `Map` changes JSON serialization and key access; removing an `async` wrapper can turn a rejection into a synchronous throw; replacing `for...of` with `filter` changes sparse-array behavior and effects. A donor example does not prove equivalence. Prefer clarity to fewer lines.

Simplify before final verification. A no-op is valid when the code is clear or the safety of a cosmetic change is unproven. A reviewer returns findings; if the reviewer writes code, that new change receives the appropriate verification.

After the last substantive edit, run the affected checks and the project's mandatory set. Reuse applicable current CI. Do not require a test for every function, the full suite after every line, or deletion of written code merely to impose a TDD sequence.
