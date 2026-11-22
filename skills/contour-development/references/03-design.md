# Design

## Contract

Apply when a material choice, shared interface or data, costly uncertainty or quality constraint needs resolution. Reuse applicable existing decisions. Input: the requested outcome, relevant constraints, observed architecture and consumers. Output: sufficient decisions for the next step, shared contracts and integration arrangements where needed; diagrams are not mandatory.

## Execution

1. Identify users, scenarios, boundaries, and the completion target. Separate a benefit hypothesis from an obligation. The first delivery tests the foundation without reducing the agreed product scope.
2. For affected boundaries, establish component responsibilities, sources of truth, write authority, identity, states, invariants, transaction boundaries and derived copies.
3. Agree on the shared contract before dependent parallel work: inputs, results, errors, effects, retries, ordering, cancellation, and versions where applicable. Assign an owner for changes. An old implementation does not automatically define the intended target.
4. For important qualities, define the stimulus, conditions, response, and observable measure. Do not turn an invented load level or SLA into an obligation. Consider security, integrity, availability, usability, and maintainability where they affect the decision.
5. For costly uncertainty, define the question, authorized experiment, distinguishing observation, budget, and decision if the outcome is negative. Prefer existing tools; new auxiliary code follows the shared authorization policy.
6. Compare sufficient alternatives. Record the decision, reasons, consequences, and trigger for reconsideration. Application size does not imply microservices or a new stack. Defer a local decision when doing so does not force others to guess the shared contract.
7. When work needs multiple deliveries, connect them to the full agreed outcome. Each delivery has a user outcome, consumes/produces, resources, ordering and checks of real interfaces. Reconcile material requirements with the shared decisions so that no obligation disappears between deliveries. Two compatible stubs do not prove integration. A bounded decision does not require inventing a product backlog.

For a UI, check the primary path, loading, empty states, errors, retries, input preservation, and accessibility. For data, check transitional states and reconciliation; for an API, check an existing consumer. Do not copy a shared decision into every assignment.

Return a disproved assumption to the coordinator with its area of impact. A local improvement within the contract does not require a separate committee. If the design does not need to change, use it and continue.
