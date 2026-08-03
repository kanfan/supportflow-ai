# Week 4 Review: Reliable Document Ingestion Worker

Estimated reading time: 8 minutes.

## Purpose

This slice completes Issue #33 on top of Emir's merged `0004_documents`
foundation. An admin upload now travels through Redis to a Celery worker, is
scanned before parsing, and reaches either `ready` with bounded extracted text or
`failed` with a stable safe error code. Retries and redeliveries update the same
version; they do not create another version, extracted result, or terminal audit
event.

No migration, chunks, embeddings, LLM calls, or AWS resources are added.

## How an engineer should think about the worker

Treat a Celery delivery as an untrusted wake-up signal, not as business state:

```text
version UUID only
  -> reload version + document + organization relationship from PostgreSQL
  -> row lock and claim queued -> extracting
  -> open the database-owned private storage key
  -> safety scan
  -> media-specific bounded extraction
  -> commit ready/failed + system audit event together
```

At-least-once delivery is expected. `ready` and `failed` are no-op terminal
states. An `extracting` row owned by another task is also a no-op. Redelivery with
the same task ID resumes the claim, which is the worker-loss recovery path.
Retryable infrastructure failures return the owned row to `queued`; permanent
content failures go directly to `failed`. A terminal state is never committed
without its audit event.

## Build map

- `app/documents/extraction.py` defines the extractor interface and bounded
  PDF/TXT/Markdown implementations. PDF uses strict `pypdf`; text formats use
  strict UTF-8 and never execute Markdown or HTML.
- `app/documents/ingestion.py` owns row-lock claiming, tenant relationship
  reload, scan-before-parse, retry release, terminal transitions, atomic audit,
  and allowlisted operational logs.
- `app/documents/tasks.py` defines the reusable late-acknowledged Celery policy,
  worker-loss rejection, three bounded retries, exponential jitter, 60/75-second
  limits, ignored results, and sanitized retry exceptions.
- `compose.worker-loss.yaml` enables only the local/test fake-scanner gate and a
  short Redis visibility window needed to reproduce a claimed-job container
  crash quickly; normal configuration keeps a 180-second visibility timeout,
  safely above the 75-second hard task limit.
- `app/worker.py` composes the database, private storage, scanner, extractor, and
  task. Its separate publish-only client keeps API processes from constructing
  worker dependencies.
- `compose.yaml` mounts one private named volume into API and worker containers;
  the object written during upload is therefore the same object read by the
  worker.
- `scripts/smoke_document_ingestion.py` proves real API/Redis/worker processing,
  duplicate no-op behavior, and the corrupt-file safe-failure path.
- `pyproject.toml` and `uv.lock` pin the reproducible `pypdf` dependency. No
  Alembic file changes because `0004_documents` already contains the accepted
  state/ownership fields.

## Why the tests exist

- Extractor tests prove deterministic PDF text, strict UTF-8, encrypted/corrupt
  rejection, page limits, and cumulative character limits.
- Task contract tests prove a UUID-only payload, late acknowledgement,
  worker-loss rejection, bounded retry/time policy, jitter cap, and ignored
  result storage.
- PostgreSQL tests prove first delivery for all three media types, sequential and
  concurrent duplicate no-ops, same-task redelivery, transient retry, retry
  exhaustion after one real retry, tenant-owned lookup, and one existing
  version/result.
- Atomicity tests independently force ready and failed audit commits to fail and
  prove neither terminal state nor its terminal audit survives rollback.
- Safety tests assert that filenames, storage keys, hashes, raw bytes, extracted
  text, and exception payloads do not enter worker logs, audit metadata, task
  arguments, Celery results, or status responses.

## Reproduce the evidence

```powershell
uv lock --check
uv run ruff check .
uv run ruff format --check .
uv run pyright
$env:SUPPORTFLOW_TEST_DATABASE_URL = `
  "postgresql+psycopg://supportflow:supportflow@127.0.0.1:5432/supportflow_test"
uv run pytest

docker compose up --detach --build --wait
docker compose run --rm api alembic upgrade head
docker compose run --rm api alembic check
docker compose run --rm api python -m scripts.smoke_worker
docker compose run --rm api python -m scripts.smoke_document_ingestion
```

Local exact-branch evidence on 2026-08-01:

- 187 automated tests passed against PostgreSQL;
- Ruff, format, Pyright, and lockfile checks passed;
- Compose API/PostgreSQL/Redis/worker services became healthy;
- migration check, queue smoke, upload-to-ready, duplicate no-op, and corrupt PDF
  safe-failure flows passed;
- a post-smoke API/worker log scan found none of the filename or content canaries;
- a live delivery reached `extracting` with attempt `1` and a task owner before
  the worker container was killed with `SIGKILL`; after restart and Redis
  visibility recovery, that same version reached `ready` with attempt `1`,
  cleared ownership, one version/result, and exactly one terminal audit event.

## Residual boundaries

The documented database-commit-to-broker crash window still requires an outbox
or reconciler before a real pilot. Production scanning and S3 remain Week 5
adapter work. OCR, chunking, embeddings, and public reprocessing are deliberately
out of scope. A hard-killed worker may leave a row in `extracting`, but late ack,
worker-loss requeue, and same-task ownership allow the redelivered task to resume
without another result.
