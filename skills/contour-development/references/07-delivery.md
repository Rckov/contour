# Delivery, migrations, and maintenance

## Contract

Apply when the completion target requires integration, release, or observation, and for a migration, incident, or end of support. An accepted patch does not require deployment unless deployment is assigned. Input: the candidate, environment, current authorizations, criteria, data/configuration, and responsibility. Output: a precisely named state and evidence, remaining obligations, and recovery arrangements.

## Execution

1. Distinguish an accepted patch, integrated contents, a suitable candidate, release authority, deployment, audience availability, and verified operation. Do not transfer status automatically between them.
2. Bind the release artifact to the verified candidate. Identify the audience, account and environment, configuration, compatibility with existing clients and data, infrastructure, background jobs, access, resources, support, and next owner as applicable. A checked tag is not an immutable artifact; a rebuild needs verified correspondence or new candidate checks. Artifact provenance does not prove correctness.
3. Before a material external effect, persist intent, material parameters, and operation identity in the existing `.contour` record, or verify an equivalent platform log; replace secrets with protected references. Define verification, stopping, observation, and authorized recovery. Use the existing release process or CI and link its primary evidence. Do not create a custom orchestrator or duplicate release dossier when suitable sources exist.
4. For a migration, check representative data, mixed versions, intermediate states and concurrent writes. Define reconciliation, remaining steps, retries, compensation and the permissible rollback boundary; a destructive transition requires authorization that covers it. Verify the recovery method, not merely the existence of a backup, and account for valid writes made after that backup. Use the agreed downtime and data-loss bounds; do not invent them. Reverting a binary does not restore deleted data.
5. On an error, distinguish failure without an effect, a partial result, and an unknown outcome. Reconcile the receiving system using an identifier, receipt, or state. Do not unnecessarily repeat a part known to have succeeded. If the outcome remains unknown, retry the same operation only with a verified guarantee from the receiving system that an unacceptable duplicate effect cannot occur: the same parameters, resource/environment, identity, and valid deduplication window/scope, or proven idempotency properties; authority and budget must remain valid. A field named `key` does not provide that guarantee by itself. Without a sufficient basis, block the dependent step. Compensation is a separate action with its own authority and verification.
6. After release, check the target environment's criteria and relevant user signals. Establish telemetry freshness, an appropriate observation window and sufficient representative activity or load. Success of a deployment command does not prove useful operation; absence of traffic does not prove a successful canary. Use the appropriate completion target for a CLI, library or demonstration.

## Incident and handoff

Preserve impact, known facts, uncertainty, owners, and active effects. Perform authorized containment; verify restoration and correction of the cause separately. Urgency does not turn a skipped test into an executed one. Hand off the state, limitations, observation, and next owner. Do not automatically assign round-the-clock on-call coverage.

Publishing an application does not authorize publication of all source code, logs, dumps or build metadata. Restrict sensitive artifacts, access and retention according to applicable project rules. At decommissioning, account for consumers, export or migration, traffic and jobs, access, resources, data and copies, unfinished operations and support obligations. Give remaining obligations, supported versions, exceptions and archives an owner; send notifications only within granted permission. Closing a task record does not remove an active contract.

If a specific next effect lacks authorization, complete the preparation as a concrete, reviewable plan and ask only for what is missing. Do not request authorization that has already been granted. Independent development may continue.
