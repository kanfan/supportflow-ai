# R1 durable dispatch contract (Accepted; implementation pending)

Updated: 2026-09-12. Companion to the [roadmap](./distributed-reliability-plan.md).
Approximately 5 minutes. This specifies future behavior, not implemented claims.
Accepted for R1 only through Emir's PR #55 review at `725aca6` on 2026-09-12.
[Issue #56](https://github.com/kanfan/supportflow-ai/issues/56) tracks delivery:
Eray implements, Emir reviews. Later Kafka/observability packages remain proposed.

## Runtime and lifecycle

The Celery relay is a separate service in normal Compose, using the same image
and PostgreSQL/Redis configuration as API/worker. Kafka and its optional profile
are not dependencies. Start after migrations; retry unavailable dependencies
with bounded backoff. Provide process liveness and readiness for the polling
loop plus DB/Redis connectivity, and expose last successful poll/oldest backlog.
An idle queue is healthy. Upload acceptance depends on committed durable intent,
not relay readiness; readiness failure and backlog growth must be visible.

On SIGTERM stop claiming work, finish or abandon in-flight publication within a
bounded grace period and close clients. Expiring DB leases recover unfinished
claims; a stale lease token cannot mark another relay's publication successful.

## Published versus processed

Persist one immutable task ID with each version's ingestion intent and reuse it
for relay retries and reconciliation. Celery task IDs alone do not deduplicate.
Preserve the worker's atomic DB claim, task ownership, retries, terminal no-ops
and atomic terminal audit. Concurrent duplicates must not cause duplicate DB
effects; repeat extraction reads may occur and are not an exactly-once claim.

`published_at` means the broker accepted the publish, not that a worker began
or completed it. PostgreSQL version state/ownership is the processing authority;
Celery results are ignored. `ready` or explicit terminal `failed` settles the
intent; `published + queued` remains eligible for reconciliation.

Use a dedicated non-evicting Redis queue with a persisted volume and AOF for the
reliability evidence profile. Document/test the actual fsync setting and restart
behavior. Broker acknowledgement does not guarantee survival of every Redis
failure; volume loss or an fsync window may lose tasks. The PostgreSQL intent
remains the recovery source while its DB and required document storage survive.

A bounded periodic reconciler selects published intents whose version remains
queued beyond a configured no-start deadline. Set that deadline above normal
queue delay, retry backoff and visibility timeout, and report capacity backlog
separately. Recheck version/ownership in a transaction, rate-limit republication
and reuse the original task ID. A worker can claim after this check: its existing
duplicate/ownership guard remains mandatory, never reset active ownership.
Preserve retry counters; successful republish resets the reconciliation timer.

An `extracting` version is not a never-started task. Wait through the configured
worker hard limit plus visibility/recovery margin. Recovery of stale extracting
work must reuse its persisted owner/task ID and the existing safe redelivery
path, without clearing ownership or racing a live worker. R1 must demonstrate
this path after broker task loss; ambiguous ownership is surfaced for operator
review rather than forcibly reset. Recovery deadlines and max attempts must
be explicit configuration with bounded retries and alerts, not infinite loops.

Required tests include publication followed by task removal before first start,
worker claim racing reconciliation, and Redis restart/loss after claim. Prove
eventual terminal state or an explicit unresolved recovery failure in a bounded
test window. Never describe broker publication alone as loss-free completion.

## Retention and rollout

Pending, leased, published-but-unprocessed and unresolved recovery intents are
never age-deleted. Terminal Celery intents become cleanup candidates seven days
after terminal completion, only with no active lease or recovery action. Keep
stable task identity/terminal deduplication for at least the broker retry/replay
window; reject replay outside supported retention. For future Kafka rows,
published cleanup additionally requires the accepted snapshot/offset recovery
boundary and seven-day replay policy. Outbox cleanup is not an event archive.
Batch cleanup and expose disk/backlog pressure rather than deleting pending work.

Deploy an additive migration with a unique logical ingestion-intent key. Pause
uploads, drain old producer transactions and quiesce worker intake/in-flight work
before capturing a rollout cutoff. Start new producer/worker/relay code only
after compatibility checks and backfill. Within that bounded cutoff backfill
only legacy queued versions without an intent, using insert-on-conflict no-op
and row-lock/state rechecks. Preserve any known task ID; assign one once only
when ownership is absent. Do not reset extracting/ready/failed versions or retry
all failures. Queued legacy broker messages may still arrive; DB claims must
make old/new deliveries converge even when a legacy ID cannot be recovered.

Resume worker/relay and uploads after reconciling backfill counts. Retry the
backfill safely with the same cutoff/unique key. Record skipped active versions
for separate inspection. Rollback pauses producers/relay and retains intents;
do not revert to an old direct-publish producer or drop the outbox migration
while accepted intents are outstanding. R1's evidence and review complete its
own gate before choosing the next product or Kafka milestone.
