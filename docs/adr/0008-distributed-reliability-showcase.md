# ADR 0008: Distributed reliability portfolio extension

- Status: Accepted for R1 only; R2-R4 remain Proposed
- Date: 2026-09-09
- Scope: roadmap decision only; no runtime dependencies added

## Context

The modular monolith already has tenant isolation, transactional audit and
worker crash recovery. ADR 0005 documents a remaining DB-commit-to-dispatch
crash window. Live AWS deployment is deferred. The next portfolio investment
should demonstrate useful reliability properties with reproducible evidence.

## Decision (R1 accepted; later packages proposed)

Adopt the [distributed reliability plan](../distributed-reliability-plan.md):

1. Close durable ingestion dispatch with a PostgreSQL transactional outbox
   and a normal-Compose Celery relay before introducing another broker.
   Apply the [R1 lifecycle/recovery/retention contract](../r1-durable-dispatch-contract.md).
2. Add an optional Apache Kafka/KRaft lab for versioned ticket-status events
   and a rebuildable tenant-scoped projection, with a transactional inbox,
   durable quarantine/DLQ and controlled replay.
3. Add OpenTelemetry/Tempo traces, Prometheus/Grafana metrics and correlated
   structured logs, followed by k6 and deterministic crash/replay evidence.

Keep the modular monolith, Redis/Celery, AWS infrastructure code and original
AI/RAG/human-review deliverables. Introduce no Kubernetes, microservice split,
managed Kafka or paid resources in this increment. Separate optional Compose
profiles keep the default development environment affordable in RAM and cost.

The transport is at least once. Atomic DB inbox + side effects make tested
replays idempotent; they do not guarantee exactly-once external side effects.
Three provisional lab SLOs and the evidence workload are specified in the plan.
Claims require recorded measurements; mock tests are not deployment evidence.

## Consequences and acceptance

This changes the earlier roadmap exclusion of Kafka/event buses for the
portfolio extension. Current behavior in ADR 0005 is unchanged until R1 merges.
The Markdown addendum supersedes the PDF's old schedule for this extension.
Remaining weekly dates need re-estimation rather than silently expanding the
original deadline. Ownership is proposed, not assigned to Emir unilaterally.

Tradeoff: another broker and telemetry stack add operational and testing work.
The projection/replay demonstration must justify that cost. If the optional
profile is too heavy, retain R1 and telemetry first; reconsider the broker in
review without replacing the whole stack. Redpanda is an alternative subject
to resource, client-compatibility and license review, not a second requirement.

On 2026-09-12 Emir approved PR #55 at `725aca6` for bounded R1 only.
Eray implements and Emir reviews transaction boundaries, rollout and recovery
evidence under [Issue #56](https://github.com/kanfan/supportflow-ai/issues/56).
This accepts the R1 contract, not its implementation or completion. Deliver
schema/transaction foundations first without changing upload behavior, then
upload/relay/reconciliation integration and recovery evidence. R2-R4 require
a separate joint milestone, estimate and ownership decision after R1.

R1 is independently shippable with its own evidence. Publication is not
processing completion; lost-task reconciliation preserves worker ownership.
R2 rebuild requires a complete ticket snapshot with a coordinated offset
boundary plus later creation/status events, including never-transitioned tickets.
After R1, explicitly select Kafka R2 or the original AI/RAG milestone, record
new estimates and jointly accepted ownership. R2-R4 remain proposed; R1
approval alone does not commit either contributor to the full extension.

## Post-R1 decision (2026-09-17)

R1 is complete: #58 merged as `a7cd892`, main CI run `35194005743` passed,
and #56 records acceptance and recovery evidence. This supersedes the earlier
pre-implementation status above.

Following Eray's AI/RAG proposal, Emir selected AI/RAG before Kafka. The next
bounded work package is [A1, Issue #59](https://github.com/kanfan/supportflow-ai/issues/59):
Emir implements and Eray reviews the proposed
[classification contract](../a1-classification-contract.md). Its technical
defaults await PR review before implementation. A1a is provisionally estimated
at 2-3 focused sessions plus review; later dates require re-estimation.
R2-R4 and live AWS remain deferred; A2/A3 assignments remain proposals.
