# Distributed reliability and observability plan

Updated: 2026-09-12. Status: R1 accepted in ADR 0008; R2-R4 remain proposed.
Reading time: approximately 8 minutes. No new runtime capability is claimed.

## Purpose and precedence

Extend the open-source portfolio with demonstrable delivery guarantees,
recovery, observability and measured performance. Keep the support workflow,
tenant isolation and AI/RAG milestones: infrastructure must support a usable
product. Live AWS deployment is deferred; AWS Terraform/adapters/pipeline code
remain in scope. This work runs locally and in bounded CI jobs.

This is the current roadmap addendum to the 29-page Revision 1.3 PDF. For the
new event-bus scope, execution order and deferred AWS window, this addendum
supersedes the older PDF schedule and README exclusions. The PDF remains the
historical baseline; its coordinate-based AWS revision generator is unchanged.
ADR 0005 describes the currently implemented dispatch behavior until the
outbox implementation merges. Proposed behavior below is not current behavior.

## Architecture and scope

Maintain one repository and modular monolith with independently runnable
relay/consumer processes. PostgreSQL is the source of truth. Redis/Celery
continues to execute ingestion/AI jobs and store sessions. Choose Apache Kafka
in single-node KRaft mode for the optional event-stream lab; review/pin the
image and Python client at implementation time. Do not run Kafka and Redpanda
together. Single-node persistence/restarts do not prove broker HA or quorum
failure tolerance.

First fix durable ingestion dispatch using an outbox destination for Celery.
If R2 is selected after the R1 review, add `ticket.created.v1` and
`ticket.status_changed.v1` events plus a baseline snapshot for a rebuildable
tenant-scoped ticket-status projection in PostgreSQL. The projection is an
eventual read model for demonstration/diagnostics, not authorization state or
the source used for ticket mutations. No remote LLM calls or customer messages
are replayed. This gives Kafka a real replay use case without duplicating the
existing worker's job.

The normal Compose path includes the Celery outbox relay; it starts without
Kafka and is required for durable uploads. Its lifecycle, processing recovery
and rollout are defined in the [R1 contract](./r1-durable-dispatch-contract.md).
A `reliability` profile adds only Kafka and its relay/consumer; an
`observability` profile adds OpenTelemetry Collector,
Tempo, Prometheus and Grafana. Record CPU/RAM limits and actual usage. Bring up
these profiles only during development or evidence runs. Kubernetes, EKS,
microservice extraction, CDC/Debezium and a schema-registry service are outside
this increment. AWS MSK or paid observability services are not required.

## Delivery sequence and review gates

These are gated work packages, not additional promises against the old weekly
dates. Re-estimate the remaining calendar with both contributors after R1;
do not silently claim the original Week 12 deadline still fits expanded scope.

| Package | Proposed lead / reviewer | Deliverable and exit gate |
| --- | --- | --- |
| R1: durable dispatch | Eray / Emir (bounded approval required) | Independently shippable outbox, normal-path relay and recovery evidence within the stated Redis/worker assumptions |
| R2: event processing | Emir / Eray | Kafka status events, transactional inbox/projection, retry and quarantine/replay; duplicate/order/tenant tests |
| R3: telemetry | Eray / Emir | Correlated traces, bounded metrics, structured logs and dashboards across API/relay/consumer/worker |
| R4: evidence | Shared; Emir reviews | k6 scripts, crash/replay harness, SLI reports and a reproducible short local demo |

R1 includes its own crash/recovery evidence and minimal health/backlog signals;
it does not wait for R2-R4. After R1, both contributors record its results,
capacity/time estimate and an explicit choice: Kafka R2 or the original
classification/RAG milestone next. Record lead/reviewer and revised dates for
that selected package in a GitHub issue before starting it. No decision means
R2-R4 stay deferred, not an automatic queue ahead of product work.
R2/R3/R4 assignments remain proposals; accepting R1 does not accept them.
R2 depends on R1's contract; full R4 depends on the selected stable components.
Continue classification, tenant-filtered RAG, citations/no-answer evaluation
and human approval from the original roadmap; reliability work does not close
or replace these product milestones. #37/#39 code review continues separately;
the live #40 gate is deferred and does not block local R1-R4.

## Transactional outbox contract

Insert the domain change, synchronous audit event and outbox row in the same
SQLAlchemy transaction. Rollback leaves none of them committed. For upload,
storage compensation still handles DB failures; a committed `queued` version
returns 202 with durable dispatch intent even while the broker is unavailable.
Replace the current `dispatch_failed` compensation only in the R1 API change,
with backward-behavior notes and regression tests. Backfill legacy queued rows
idempotently; do not retry all existing failed rows automatically.

Envelope: immutable event UUID, tenant UUID, aggregate UUID and monotonic
aggregate version, event type/schema version, timestamp and optional validated
trace context. Use allowlisted IDs/status enums, not document text, names,
ticket bodies, credentials or full request dumps. Destination identifies
Celery versus Kafka; API code does not publish directly after commit.

The relay claims bounded batches using DB locking and expiring leases, then
publishes outside long-running DB transactions. A lease token fences stale
relay updates. Mark `published` only after broker acknowledgement; this is
not `processed`. The R1 contract defines lost-task reconciliation. A crash after
acknowledgement can publish twice: delivery is at least once. Track attempts,
next attempt, safe failure category and oldest pending age. Stop/retry on
broker outages without discarding durable rows. Set bounded batch size,
backoff/jitter and shutdown behavior. Test competing relays and expired leases.

Use `(tenant_id, aggregate_id)` as the Kafka key. Partition order is not enough
if parallel relays publish out of order; the status projection must use the
aggregate version and reject older updates. Version assignment is atomic with
the source mutation. Do not assert global event order.

## Consumer, DLQ and replay contract

Within one PostgreSQL transaction insert a unique inbox key
`(consumer_name, tenant_id, event_id)`, update the projection and record any
consumer audit effect. Commit Kafka offsets only after DB commit. On duplicate
delivery, the unique constraint prevents another side effect; concurrent
duplicates and process death after DB commit must be tested. Composite tenant
constraints and authorized reads protect the projection; envelope tenant IDs
must match the authoritative aggregate. Broker consumers are trusted internal
processes, not tenant-accessible APIs.

Transient failures receive bounded retries. Broker/DB outages pause consumption
without treating every event as poison. Permanently invalid/unsupported events
go to a durable PostgreSQL quarantine (the first DLQ), preserving safe original
envelope identity, failure category and attempts before advancing the offset.
If quarantine persistence fails, do not commit the offset. This avoids a second
unsafe Kafka-to-DLQ dual write. Define ordering/gap behavior explicitly: this
latest-version status projection may advance past quarantine, while the
quarantined event remains visible and unresolved.

Provide a rate-limited operator replay command with dry-run, explicit tenant,
bounded event selection, schema validation and replay audit. Preserve original
event IDs and use the same idempotent handler. No blanket inbox deletion or
offset reset. For a full projection rebuild, use a new projection generation
and consumer namespace with the snapshot boundary below. Compare canonical
per-tenant state hashes, then promote the generation after validation. A
duplicate replay into the existing generation should be a no-op.

### Rebuild baseline and completeness

The initial local implementation uses a brief write barrier, not a timestamp
guess: pause ticket writes and await in-flight transactions, drain the ticket
outbox to broker acknowledgement, then record Kafka end offsets per partition
and snapshot all tickets as `(tenant_id, ticket_id, status, aggregate_version)`
under the same barrier. Include tickets that have never changed status. Store
the snapshot checksum, schema version and offset vector together, then resume
writes. New tickets emit creation events; transitions emit full current status
with a monotonically increasing version. Snapshot and boundary capture failure
aborts the rebuild; do not promote a partial baseline.

Seed the new generation from that snapshot and consume from the recorded next
offsets; duplicate or older versions cannot overwrite newer state. At a second
write/drain barrier, catch up to its offset vector and compare sorted canonical
tuples, counts and hashes per tenant with the authoritative DB at that barrier.
Only then promote. Keep ticket deletion out of scope until tombstone events
exist. If any required offset expired, obtain a fresh snapshot; retained status
events alone cannot prove completeness or recreate never-transitioned tickets.

Record replay coverage and retention: initial local target is seven days of
Kafka data and at least that long for inbox deduplication records, plus a
24-hour retry margin. Full-history claims require a retained snapshot/event
corpus; expired events cannot be reconstructed from the broker. Failed events
remain quarantined until resolved or explicitly disposed under a documented
retention action. Bound local disk use and expose backlog/disk pressure.

## Observability and SLO candidates

Propagate W3C trace context through outbox and message headers; use span links
for replay/batch work. Cover API -> DB -> relay -> broker -> consumer/worker.
Send OTLP to Collector and Tempo; expose Prometheus metrics and versioned
Grafana dashboards. Structured JSON logs include trace/span/request IDs and
stable error categories. Do not log bodies, SQL parameters, secrets or file
contents. Do not use tenant/user/ticket/event IDs as Prometheus labels.

Track request duration/failure, pool/queue saturation, oldest outbox age,
consumer lag, retry/quarantine counts, duplicate suppression and processing
duration. Telemetry outages must not block business transactions. Use bounded
export buffers, sampling and retention; show telemetry loss explicitly.

Initial engineering targets below are provisional until the first baseline.
The normal run is a named 30-minute synthetic workload after warmup, repeated
three times on recorded hardware. Fault runs are reported separately, never
silently excluded from a combined result. They are lab SLO candidates, not a
production availability commitment or proof of a 30-day service SLO.

| Objective | SLI definition and provisional threshold |
| --- | --- |
| API success | >=99.9% of valid authenticated ticket read/write attempts return the expected response; timeouts, unexpected 4xx and 5xx count as bad |
| Interactive latency | >=95% of those attempts finish within 300 ms; failed/time-out attempts are bad, measured per operation as well as aggregate |
| Event freshness | >=99% of valid committed status events are reflected or superseded by a newer version within 10 s, counting undelivered/unresolved events as bad at the deadline |

Report numerators, denominators, run window and error budgets (0.1%, 5%, 1%).
Allow a deadline drain period for events committed at the end of the window.
SLO breaches fail the evidence gate and require an explanation or a reviewed
target change. Do not redefine the cohort after seeing results.

## Load and fault evidence

k6 must use actual HTTP over the container network. The existing Week 3
TestClient benchmark is useful but not comparable network/load evidence.
Use a fixed seeded dataset (initial target: 10 tenants, 10,000 tickets, 50,000
messages), fixed read/write mix (80/20), separate read/detail/message/status
results, and authorized tenant-specific users. Login/token refresh behavior is
explicit; dataset cleanup is limited to a dedicated local test DB.

Run stepped 10/50/100/250/500 virtual-user scenarios with documented think
time, plus separate constant-arrival-rate runs to reveal saturation. A VU is
not the same as an in-flight request or RPS. Report achieved request rate,
dropped iterations, errors, p50/p95/p99, CPU/RAM, DB connections, queue lag,
versions, image digest, limits and client placement. A 500-VU run is an
experiment, not a promised result; report the last sustainable step. Run heavy
load/fault tests manually; keep only bounded smoke/contract tests in PR CI.

Required deterministic fault cases: kill after DB commit before publication;
kill after broker acknowledgement before outbox marking; kill consumer after
DB commit before offset commit; duplicate/concurrent and out-of-order events;
broker restart; malformed/unsupported event; DB outage during quarantine;
replay twice; full projection rebuild; cross-tenant event/lookup rejection.

For the finite seeded corpus require zero lost accepted intents, zero duplicate
DB/audit side effects, and 100% valid-event projection convergence after drain.
Reconcile original event IDs and per-tenant canonical hashes, not counters
alone. Report how many events/fault cycles were checked, quarantine exclusions,
recovery duration and deadline failures. No universal exactly-once guarantee;
external API/LLM side effects would need their own idempotency contract.

Deliver scripts, machine-readable results, CI/run links, one representative
trace, dashboards and an under-10-minute review explaining the transaction
boundaries, test purpose, crash outcomes and limitations. CV claims use only
these measured results. Broker HA, live AWS and sustained production SLOs
remain explicitly unverified.

## Technical references

- [Transactional outbox](https://microservices.io/patterns/data/transactional-outbox)
- [Idempotent consumer](https://microservices.io/patterns/communication-style/idempotent-consumer.html)
- [Apache Kafka Docker](https://kafka.apache.org/42/getting-started/docker/)
- [OpenTelemetry instrumentation](https://opentelemetry.io/docs/concepts/instrumentation/libraries/)
- [k6 load models](https://grafana.com/docs/k6/latest/using-k6/scenarios/concepts/open-vs-closed/)
- [k6 thresholds](https://grafana.com/docs/k6/latest/using-k6/thresholds/)
