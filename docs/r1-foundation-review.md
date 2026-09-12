# R1 foundation review (Issue #56, slice 1)

Reading time: about 3 minutes. This is a schema/transaction foundation, not
completed durable dispatch. Upload still uses the existing direct publisher.

## What changes and why

- `app/documents/models.py`: `DocumentIngestionIntent` stores only IDs and a
  creation time. A composite foreign key binds each version to its tenant.
  The tenant/version unique constraint also indexes FK lookups; a unique task
  ID prevents unrelated jobs sharing one delivery identity. No bodies or
  storage secrets belong in this table.
- `0005_ingestion_outbox`: additive migration after verified `0004_documents`.
  A trigger prevents rewriting identity, including through raw SQL. Downgrade
  locks the table and refuses to drop it if any intents remain. Never delete
  pending rows merely to bypass this guard.
- `app/documents/outbox.py`: tenant-scoped repository and a new-upload helper.
  It flushes parents, synchronous audit and intent within the caller's existing
  transaction. It never commits, publishes, or performs storage I/O. The caller
  must commit or roll back the entire operation; this is not a nested savepoint.
  Duplicate inserts fail rather than silently creating another audit event.
- `tests/integration/test_ingestion_outbox.py`: actual PostgreSQL constraints,
  raw-SQL identity mutation rejection, duplicate IDs, tenant-scoped reads,
  downgrade safety and post-flush rollback checked through a fresh session.
- `tests/test_ingestion_outbox.py`: inexpensive input and data-minimization checks.

## How to reason about the boundary

Storage remains outside the database transaction. For this helper, either the
document/version, audit and intent all commit or all roll back. The test queries
all four tables before injecting failure, then uses a new session to prove no
rows survived. A mocked exception before flush would not prove that property.

Persisted intent is not broker acknowledgement, and acknowledgement is not
completed extraction. The task ID is not worker ownership or deduplication by
itself. Existing worker ownership/terminal guards stay unchanged.

## Build and verification

Use the existing locked project environment and disposable `supportflow_test`
PostgreSQL database described in README. Run:

```sh
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest tests/test_ingestion_outbox.py tests/integration/test_ingestion_outbox.py
uv run pytest
```

The integration fixture executes migration upgrade/check/downgrade/upgrade and
cleans only its disposable database. CI also runs the unchanged Compose smoke
flows, checking that the additive migration does not switch upload behavior.

## Still required before R1 acceptance

The integration slice must add publication/lease/recovery lifecycle columns
and indexes with their concrete queries, atomic producer switch, fenced relay,
reconciliation, retention, backfill, Compose health and actual fault evidence.
This foundation intentionally does not choose numeric lease/retry settings or
pretend to recover lost broker messages. Keep Issue #56 open. AWS is deferred;
Kafka versus AI/RAG remains a joint decision after completed R1 evidence.
