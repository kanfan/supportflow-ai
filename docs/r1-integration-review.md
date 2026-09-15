# R1 durable upload and recovery review

Reading time: about 7 minutes. Issue #56 remains open pending joint evidence
review. No Kafka or live AWS execution is included.

## Workflow and engineering boundaries

Upload writes private storage first, then commits document/version, synchronous
audit and immutable intent together. DB failure rolls back all DB rows and
compensates storage. Broker failure does not prevent HTTP 202. A relay in normal
Compose polls PostgreSQL without Kafka and publishes only the version UUID with
the persisted Celery task ID. Publication is NOT processing completion.

The relay claims one due row with `FOR UPDATE SKIP LOCKED`, commits a random
lease token, publishes outside that transaction, then conditionally records the
result only while that same lease is unexpired. Competing/crashed relays may
publish duplicates; terminal version/audit effects are protected by the worker.

The worker holds a PostgreSQL session advisory lock for the entire delivery,
including retry preparation. Its dedicated connection has no open transaction
during extraction. This also prevents a same-task-ID replay from overlapping
an active delivery. A process kill releases the connection lock. An ambiguous
persisted owner is not forcibly cleared. The worker retains its durable attempt
count; recovered Celery headers preserve retry position and the DB caps attempts.

## Numeric limits and failure behavior

- Poll: 2 seconds by default (1-10 configurable); one publish per poll.
- Relay lease: 30 seconds; broker connect/socket timeout 2 seconds, publisher
  retry disabled. DB relay statements/lock waits bounded by a 2-second statement
  timeout. SIGTERM stops new claims; normal shutdown grace is 30 seconds.
- Publication attempts: 20 maximum, capped exponential retry delay 2-60 seconds.
  After a previous successful publication, at most 3 recovery claims. Exhaustion
  retains the unresolved intent; it does not mark the document terminal. A
  bounded, audited operator grant is available below; counters are never reset.
- Queued reconciliation: default 1200 seconds. Effective delay is at least
  visibility + hard-limit * (max-retries + 1) + 60 * max-retries + 60 seconds.
  Increasing worker/broker limits automatically increases this floor.
- Extracting reconciliation: hard limit + visibility + 60 seconds (375 by
  default). Reuse the original owner ID without resetting status or counters.
  A worker that is still alive is protected by the advisory lock.
- Terminal settlement is observed conservatively by the relay, then retained
  for at least seven days. Late terminal completion also settles an exhausted
  intent once its relay lease is absent/expired, clears unresolved state and
  starts a fresh seven-day retention window. No republish/grant is needed.
  No cleanup of pending/unresolved or leased intents.
  Retained terminal version rows still reject old broker deliveries after cleanup.

`127.0.0.1:8091/live` and `/ready` are relay-local health endpoints, not public
API routes. They expose polling freshness, backlog, oldest creation timestamp
and unresolved count, without document contents or credentials. Idle is healthy;
dependency failure makes readiness fail while the process continues bounded
polling. Unresolved count > 0 requires inspection, not silent automatic reset.

## File-by-file build

- `0006_outbox_lifecycle` / `documents/models.py`: lifecycle fields, closed safe
  errors, lease-pair/counter constraints and due/retention indexes; downgrade
  refuses to discard existing intents.
- `documents/service.py`: atomic upload switch using the accepted #57 helper.
- `documents/relay.py`: short claims, fenced completion, reconciliation,
  seven-day cleanup and cutoff-bounded idempotent backfill.
- `documents/relay_runtime.py`: process-owned clients, local health, shutdown
  and CLI. `compose.yaml`: migration prerequisite and normal relay service.
  API, worker and relay ALL wait for successful migration; migrate depends only
  on PostgreSQL. CI also proves a deliberately failing migration starts none of
  those application processes in a disposable isolated Compose project.
- `documents/ingestion.py`, `tasks.py`, `worker_runtime.py`: delivery lock and
  durable retry cap; existing terminal audit transaction remains authoritative.
- New recovery tests and `smoke_outbox_recovery.py`: loss/race evidence, not
  claims about exactly-once effects, high availability or production reliability.

## Safe local rollout / rollback

For an existing database, pause upload traffic, stop the old relay (if present),
and gracefully stop/drain old workers before recording an aware UTC cutoff.
Do not infer a safe live rollout from these local commands.

1. Keep a DB/storage backup. Apply migrations with new code:
   `docker compose run --rm migrate`.
2. Keep API traffic and worker intake paused. Run
   `docker compose run --rm relay python -m app.documents.relay_runtime --backfill-cutoff <UTC-ISO-cutoff>`.
   Repeat with exactly the same cutoff until the bounded 100-row batch returns
   zero. It only selects queued versions at/before cutoff without intent. Existing
   task identity is preserved; failed/extracting/ready versions are not reset.
   Inventory skipped active/failed rows separately before resuming traffic.
3. Start the new worker and relay, verify health/backlog, then resume uploads.
   Legacy queued messages may arrive; worker locking/terminal guards apply.

Rollback pauses uploads and relay, drains workers, and retains schema/intents.
Restore only a compatible durable-dispatch build; do NOT restore the old
direct-publish API or drop migrations while intents exist. Outstanding intent
inspection and compatible recovery are required before reopening traffic.
Backfill is not a general retry-all command. Never delete intents to force a
downgrade. Cleanup is not an event archive or a replay API.

## One-intent operator recovery after exhaustion

This CLI is for trusted operators already holding DB access, not a public API
or a way to authenticate as another user. Attribute the action to your own active
admin membership in the selected tenant. Stop the relay for inspection; repair
the underlying broker/storage/scanner problem first. Never force-clear worker
ownership. An active worker lock, live relay lease, ambiguous/recent extraction,
terminal version, exhausted worker budget or third-used grant is rejected.

Inspect ONE tuple with the following local command; replace only the UUIDs:

```sh
docker compose exec -T postgres psql -U supportflow -d supportflow -c "SELECT i.id, i.organization_id, i.task_id, i.last_error, i.unresolved_at, i.lease_expires_at, i.publish_attempts, i.recovery_attempts, i.recovery_grants, v.status, v.processing_task_id, v.processing_started_at, v.attempt_count FROM document_ingestion_outbox i JOIN document_versions v ON (v.organization_id,v.id)=(i.organization_id,i.document_version_id) WHERE i.organization_id='<tenant-uuid>' AND i.id='<intent-uuid>';"
```

After diagnosis, repair and confirmation that no worker owns active execution:

```sh
docker compose run --rm relay python -m app.documents.relay_runtime --recover-intent <intent-uuid> --organization-id <tenant-uuid> --actor-user-id <your-admin-user-uuid> --dependencies-repaired
docker compose up --detach --wait relay
```

The CLI checks Redis connectivity before locking, validates active tenant/admin
membership, checks authoritative state under row + worker-compatible advisory
locks, then atomically appends `document.dispatch_rearmed` with allowlisted
`dependency_repaired` reason and grant number. One grant adds at most one extra
publication/recovery allowance. Maximum three grants per intent; immutable task
identity and every worker/publication/recovery counter remain unchanged. There
is no retry-all, arbitrary reason/body input or counter-reset option. DB/audit
failure rolls back the grant. If limits or ownership prevent rearm, leave the
intent unresolved for investigation; do not bypass protections with raw UPDATE.

Observe the selected version and relay health until terminal or unresolved.
Inspect the selected version's audit events through the existing admin audit
endpoint. Later terminal completion is reconciled automatically even if the
intent was unresolved; retention begins only after that reconciliation.

`0006` is still an unmerged revision: rerun the disposable migration round trip
when updating from an earlier #58 head rather than treating its old local
schema as a deployed migration contract.

## Reproduction and evidence limitations

`uv run pytest` with the README's disposable PostgreSQL/Redis test configuration
runs the real route post-flush rollback, tenant/identity constraints, competing
relays, stale fencing, lost-queue reconciliation, active delivery lock,
extracting recovery, retention and cutoff tests. Ruff/format/Pyright remain
required. Local Docker was stopped during implementation; CI supplies DB and
Compose evidence, and final results are recorded in the PR at an exact head.

The CI workflow additionally runs real Compose uploads and SIGKILL/redelivery.
Its new fault steps stop worker/relay, remove only the target task from Redis,
republish via the outbox and assert exactly one terminal DB/audit effect. The
claimed-loss case restarts Redis and explicitly shortens only the test relay's
stale deadline AFTER SIGKILL; it does not measure the normal recovery latency.
Normal runtime does not expose that zero-deadline override.

Compose's dedicated `redis-queue` uses a persisted volume, AOF `everysec`, and `noeviction`. AOF
can lose the last fsync window, and the tests deliberately remove broker data:
PostgreSQL plus source storage must survive for recovery. Sessions use the
separate `redis` service. Live AWS validation remains deferred. No measured
throughput/SLO claim is made here.
