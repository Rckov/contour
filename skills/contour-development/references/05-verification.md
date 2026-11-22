# Checks, review, and acceptance

## Contract

Input: the active requirement, a specific candidate and basis, criteria, current checks, and limitations. Output: observed verification results, substantive findings, and a separate decision by the authorized acceptor. Do not treat an absence of findings as proof that no defects exist.

## Verification

1. Connect each criterion to an observable property, the correct expected result, a method, and evidence. Verification concerns the requirement; validation concerns its intended user use. Apply what the requested endpoint needs. A coverage percentage or a test that mirrors the implementation does not define correct behavior. For a small reversible edit, inspection of the artifact may suffice; do not create an unnecessary automated test.
2. Verify actual execution, completion, and selection. Use `PASS`, `FAIL`, `INCOMPLETE`, `NOT_RUN`, and `INCONCLUSIVE`; lost evidence means an unconfirmed outcome, not a proven NOT_RUN. An empty selection, skipped checks, or a neutral wrapper status does not confirm that checks ran.
3. Bind evidence to the relevant code, tests, test oracle, configuration, dependencies, environment, and integration baseline. A commit SHA is insufficient for a dirty working tree or external data. Reuse CI only when its applicability is established. After the last material edit, rerun the affected checks and the project's mandatory set. This does not relax the local runner's exact candidate/profile binding: a changed selected input invalidates its receipt even if the edit appears cosmetic.
4. Preserve all flaky attempts. A baseline failure remains a failure; establish its independence from the change using a comparable basis. Do not hide red results behind a green retry.
5. Separately inspect changes to controls: expected results, test selection, filters, runner, workflow, policy, and status source. Correcting an erroneous test is permitted on an independent product basis. Weakening a criterion to satisfy it is not a fix.

## Review

Provide a neutral package: intent, baseline and diff, criteria, primary results and unknowns. Use independent task context where available; do not give the reviewer the author's preferred verdict or diagnosis as a fact. The reviewer inspects the sources and assesses sufficiency. A finding identifies the location, violated condition or risk, realistic scenario, consequence and way to verify it. Check relevant counterevidence in callers, guards, tests or a comparable baseline before retaining it; distinguish an introduced defect, existing defect, risk and advice. No finding is a valid outcome. A style preference does not create a requirement; reject an incorrect finding with a reasoned basis.

Material changes involving access, money, data integrity, shared contracts, or migrations require a competent reviewer within the established scope. Establish the reviewer's ability to assess the relevant defect class; a role label or agreement between two models does not establish independence. Read-only access requires actual restrictions; where permissions are shared, explicitly describe the control as organizational. Do not present this procedure as a technical boundary. Give temporary verification outputs a permitted location under `.contour/` and keep durable review notes in the shared work record when needed.

## Decision

Classify every applicable criterion: confirmed; justifiably inapplicable; covered by a valid authorized waiver; or still unresolved. A waiver preserves the actual check status, the authority owner, consequences, compensation, and expiry or reconsideration condition. A rule that cannot be waived cannot be bypassed with a waiver.

Acceptance applies to a specific completion target and artifact. If verification is incomplete, do not describe the result as fully verified. Existing CI output or an inspected source and diff can carry the evidence; a duplicate report file adds no assurance. Direct inspection records the observer, actual source, criterion, observation and limits, and cannot replace a required executable gate or separate review. The package's `gate` checks supplied data for consistency and gaps; it does not authenticate the source or accept the product. The acceptor verifies both the sufficiency of the criteria and the existence of the evidence.

The [prepared runner](runtime-verification.md) collects actual local process outcomes and qualified unittest/JUnit selections. The controller refuses a missing, stale, failed or zero-success criterion. `LOCAL_OBSERVED` describes a trusted local observation, not protection against same-user tampering. A generic process check can verify a build/lint result, but cannot invent a test count. A native review records its actual host witness and remains separate from product execution. The local controller currently requires every registered criterion; it has no waiver shortcut. If a project permits waivers, use the existing policy's explicit review path and retain unresolved local criteria rather than changing them to obtain acceptance.

When a finding requires a fix, return the affected area to implementation and continue independent work that is already understood. After the fix, reread affected claims and check the consequences. If no defects are found, report coverage and limitations rather than an absolute guarantee.
