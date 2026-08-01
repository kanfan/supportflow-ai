# ADR 0005: Document ingestion and worker reliability contracts

- Status: Accepted
- Date: 2026-07-29
- Tracks: #32 and #33
- Prerequisite: Week 3 merged into `main` as `6a0db7c`

## Context

Week 4 adds the first untrusted file-upload and long-running background-processing
boundary to SupportFlow. The API, PostgreSQL, object storage, Redis, and Celery
worker must agree about ownership, state, retries, and failure behavior.

Implementing the document model and worker independently without a shared
contract could cause several failures:

- the API and worker could use different status values;
- two Alembic branches could both claim revision `0004`;
- task retries could create duplicate versions or extracted output;
- a worker could trust tenant or storage data supplied in a queue message;
- a user filename could become a public or traversable storage path;
- PostgreSQL could claim a job is queued when Redis never received it;
- raw documents, extracted text, storage keys, or credentials could enter logs;
- a corrupt or malicious document could run expensive parsing inside the API
  request.

This ADR extends ADR 0001 through ADR 0004. It freezes the Week 4 boundary only;
chunking, embeddings, retrieval, LLM calls, and AWS infrastructure remain later
timeline work.

## Decision

### Delivery order and ownership

Week 4 uses this dependency order:

1. This ADR is reviewed and accepted by Emir and Eray.
2. Emir implements #32 from updated `main` and owns the only initial Week 4
   migration, `0004_documents`.
3. Eray may prepare migration-independent Celery/extractor code, but rebases #33
   onto merged #32 before integrating the task with persistence.
4. Issue #33 also records the shared extraction spike and worker
   stop/start/redelivery evidence.
5. Emir reviews worker tenant, storage, audit, and failure boundaries. Eray
   reviews the model/task contract and upload-to-dispatch boundary.

The two implementation PRs remain independently reviewable:

- #32: document/version persistence, storage/scanner boundaries, secure upload,
  status API, and dispatch adapter;
- #33: extraction adapters, Celery reliability, idempotency, structured logs,
  and integrated restart/retry evidence.

### Canonical model names

The high-level sketch in ADR 0002 used `knowledge_documents`. Week 4 closes the
runtime table name as `documents` because that is the vocabulary used by the
project plan and public `/api/v1/documents` resource.

Processing state belongs to a version, not to the stable document. A future
document update can therefore process a new version without overwriting the
history of an earlier version.

#### `documents`

| Field | Rule |
| --- | --- |
| `id` | Application-generated UUIDv4 primary key |
| `organization_id` | Required tenant owner |
| `source_type` | `upload` in Week 4 |
| `external_id` | Nullable; unused for manual upload |
| `display_filename` | Safe user-facing name, never a storage path |
| `created_by_user_id` | Required active membership in the same organization |
| `created_at` | Database-generated `TIMESTAMPTZ` |
| `updated_at` | Required `TIMESTAMPTZ` |

#### `document_versions`

| Field | Rule |
| --- | --- |
| `id` | Application-generated UUIDv4 primary key |
| `organization_id` | Required direct tenant owner |
| `document_id` | Required same-organization document |
| `version_number` | Positive integer; first upload is `1` |
| `media_type` | Canonical supported media type |
| `size_bytes` | Positive integer no greater than the configured upload limit |
| `content_sha256` | Lowercase 64-character hexadecimal digest |
| `storage_key` | Randomized private object key; never returned publicly |
| `status` | Week 4 processing state |
| `extracted_text` | Nullable; internal Week 7 chunking input, never in API/logs |
| `attempt_count` | Non-negative processing-attempt count |
| `processing_task_id` | Nullable Celery delivery owner |
| `processing_started_at` | Nullable `TIMESTAMPTZ` |
| `error_code` | Nullable stable safe category; no raw exception text |
| `created_at` | Database-generated `TIMESTAMPTZ` |
| `updated_at` | Required `TIMESTAMPTZ` |

Required database protections include:

- a tenant root foreign key on both tables;
- `(organization_id, created_by_user_id)` referencing a same-organization
  membership;
- `(organization_id, document_id)` referencing the same organization's
  document;
- uniqueness for `(organization_id, document_id, version_number)`;
- uniqueness for `storage_key`;
- checks for the closed Week 4 status/media/source values, positive version,
  byte bounds, digest shape, and non-negative attempts;
- an index supporting latest-version lookup by
  `(organization_id, document_id, version_number DESC)`;
- an index supporting tenant status views by
  `(organization_id, status, updated_at DESC, id DESC)`.

`content_sha256` is deliberately not globally unique. Two tenants may upload the
same public manual, and two document identities may legitimately contain the
same bytes. Week 7 may use the hash to avoid unnecessary re-embedding within a
defined document/version workflow.

The application service creates a document and version `1` together. No public
route can create a document without a version.

### Authorization and API surface

Week 4 document management is admin-only. Agents consume derived knowledge later;
they do not upload, inspect processing failures, or select storage objects in this
slice.

```text
POST /api/v1/documents
GET  /api/v1/documents/{document_id}
```

Both routes require:

1. bearer authentication;
2. verified `X-Organization-ID`;
3. an active membership reloaded from PostgreSQL;
4. the `admin` role.

The upload uses multipart form data with one `file` field. Unexpected form fields
are rejected. Successful acceptance returns `202 Accepted`, a `Location` header,
and only:

- document ID;
- version ID and number;
- safe display filename;
- canonical media type and byte size;
- processing status;
- safe error code/message when applicable;
- timestamps.

The response never includes a storage key, content hash, extracted text, scanner
details, raw exception, task ID, or organization data supplied by the client.

The status route selects the document by both ID and verified organization, then
returns its highest version number. An inaccessible cross-tenant ID and a missing
ID return the same `404`.

### Upload validation

The configured Week 4 input limit is 10 MiB
(`10 * 1024 * 1024` bytes). The service reads the stream in bounded chunks and
stops once the limit is exceeded. `Content-Length` and the multipart media type
are hints, not authority.

Supported inputs are:

| Extension | Canonical media type | Content check |
| --- | --- | --- |
| `.pdf` | `application/pdf` | Starts with a valid PDF signature; parser performs final validation in worker |
| `.txt` | `text/plain` | Strict UTF-8 text and no NUL bytes |
| `.md`, `.markdown` | `text/markdown` | Strict UTF-8 text and no NUL bytes |

The extension and detected content family must agree. Client-supplied media type
alone cannot make a file valid. Encrypted, corrupt, unsupported, or extraction
bomb-like input fails safely in the worker.

The original filename is untrusted. The application:

- takes only a single basename;
- normalizes Unicode;
- removes or rejects control characters and path components;
- enforces a bounded display length;
- preserves only the validated extension;
- never opens a filesystem path or creates an object key from that name.

The storage key uses verified/generated identifiers:

```text
organizations/{organization_id}/documents/{document_id}/versions/{version_id}
```

The UUIDs make the key unguessable enough for naming, but authorization still
comes from PostgreSQL and application policy. Object-key secrecy is not an access
control.

### Storage and scanner adapters

Domain and application services depend on protocols, not filesystem, boto3, or a
scanner vendor:

```text
DocumentStorage
  put(key, stream)
  open(key)
  delete(key)

DocumentSafetyScanner
  scan(stream) -> clean | infected | unavailable
```

Week 4 provides:

- a local private-filesystem adapter outside static/public paths;
- a deterministic in-memory/fake storage adapter for tests;
- a deterministic fake scanner for local/test;
- a configuration guard that rejects a fake or missing scanner in staging and
  production.

The S3 adapter and real managed scanning implementation are deployment work, but
their interface is fixed now. ADR 0004 requires a private, encrypted, versioned
bucket, blocked public access, tenant-namespaced keys, and an S3 gateway endpoint.

Uploaded objects remain private and are never served directly by the API in
Week 4.

### Upload, database, storage, and dispatch boundary

PostgreSQL, object storage, and Redis do not share one ACID transaction. The
service therefore uses an explicit order and compensation:

```text
stream to bounded staging file
  -> validate type/name/size and calculate SHA-256
  -> generate document/version IDs and storage key
  -> put private object
  -> BEGIN database transaction
       insert document
       insert version(status=queued)
       add document.uploaded audit event
     COMMIT
  -> publish ingest_document_version(version_id)
  -> return 202
```

If object storage fails, no database record is created. If the database
transaction fails after storage succeeds, the service attempts object deletion
and logs only safe identifiers and an error category.

If broker publication fails after the database commit, a compensating transaction
changes the version to `failed` with `dispatch_failed` and records
`document.failed`; the API returns a safe `503` containing the document ID and
status location so the durable failure can be inspected.

There remains a small process-crash window between database commit and broker
publication. Week 4 documents and tests normal publication failure but does not
pretend to solve this distributed-systems gap. A transactional outbox or queued
record reconciler is a later reliability decision before a real pilot.

### Version state machine

Week 4 closes these states:

```text
queued -> extracting -> ready
                     \-> failed
extracting -> queued       (retryable failure before Celery retry)
queued -> failed           (dispatch failure)
```

`ready` and `failed` are terminal for automatic Week 4 processing. A future
explicit retry/reprocess operation may create a new attempt or version; no public
retry endpoint is added now.

The broader plan's `embedding` state is intentionally deferred to Week 7. In
Week 4, `ready` means safety scan and text extraction succeeded and
`extracted_text` is available for later chunking. It does not claim that chunks,
embeddings, or retrieval are ready.

### Task contract

The concrete task is:

```text
ingest_document_version.delay(document_version_id)
```

This deliberately refines the roadmap's illustrative
`ingest_document.delay(document_id)` call. A version UUID is still an ID-only
message and prevents a delayed task from accidentally processing a newer version
introduced later.

The JSON message contains no:

- ORM object;
- organization ID;
- storage key;
- filename or media type;
- raw bytes or extracted text;
- credential, token, or arbitrary options.

The worker reloads the version, its document, and tenant relationship from
PostgreSQL. Every storage access uses the database-owned organization and key.

### Atomic claim and idempotency

The task obtains processing ownership through an atomic database transition:

1. `queued` may be claimed as `extracting` with the current Celery task ID,
   incremented attempt count, and start time.
2. `extracting` with the same task ID may continue after redelivery.
3. `extracting` owned by another non-stale task is a no-op.
4. `ready` is a no-op success.
5. `failed` is a no-op unless a future explicit retry policy says otherwise.

The final state update uses the claimed task ID. A stale or duplicate worker
cannot overwrite another worker's result.

The worker updates the existing version; it never creates a new document version
or chunk. Sequential duplicates, concurrent duplicates, retries, and worker-loss
redelivery therefore converge on one version and one terminal result.

### Retry policy

Ingestion uses a reusable bounded Celery policy:

- JSON serializer only;
- late acknowledgement;
- reject/requeue on worker loss;
- explicit retry classification;
- maximum 3 retries after the first attempt;
- exponential backoff with jitter, capped at 60 seconds;
- 60-second soft time limit and 75-second hard time limit;
- ignored Celery result payload for ingestion.

Retryable failures include temporary object-storage, scanner availability,
database connectivity, and other explicitly classified infrastructure failures.

Permanent failures include unsupported or mismatched content, malware detection,
encrypted/corrupt documents, invalid UTF-8 text, extraction limit violation, and
other deterministic validation failures.

Before requesting a retry, the worker moves its claimed version back to `queued`
with a safe error category and clears processing ownership. Retry exhaustion
moves it to `failed`. A permanent failure moves directly to `failed`.

Broad `Exception` autoretry is forbidden because deterministic bad input would
consume the queue repeatedly.

### Extraction boundary

PDF, TXT, and Markdown extraction uses a media-type-specific protocol. The shared
spike selects the concrete PDF library based on:

- Python 3.13 compatibility;
- deterministic plain-text extraction;
- encrypted/corrupt input behavior;
- dependency and license fit;
- page-count and output limits;
- ability to test without network access.

The worker scans before parsing and enforces bounded execution. Default Week 4
limits are:

- maximum 250 PDF pages;
- maximum 2,000,000 extracted characters;
- the Celery soft/hard time limits above.

TXT and Markdown remain strict UTF-8. Markdown extraction returns textual content;
it does not execute HTML, scripts, macros, or remote references.

Extraction runs only in the worker, never inside the upload request. No chunks or
embeddings are created in Week 4.

Implementation outcome: Issue #33 selected `pypdf` 6.x for the PDF adapter. It
supports Python 3.13, is typed and pure Python, and uses the BSD-3-Clause license.
The adapter runs in strict mode, rejects encrypted/corrupt input, applies the
250-page limit before page extraction, and checks the cumulative character bound
after every page. OCR remains deferred.

### Audit and logging

Week 4 extends the audit vocabulary:

- `document.uploaded` with human actor;
- `document.ready` with null system actor;
- `document.failed` with null system actor.

Allowed metadata is limited to safe structured values such as version number,
canonical media type, byte size, safe error code, attempt count, and extracted
character count. It excludes filename, content hash, storage key, task ID, raw
bytes, extracted text, scanner output, and exception text.

Worker logs are structured and allowlist only:

- task name and task ID;
- document/version/organization IDs;
- attempt number;
- state transition;
- stable error category;
- duration and extracted character count.

Logs and Celery results never contain raw documents, extracted text, filenames,
storage keys, credentials, tokens, emails, request bodies, or arbitrary exception
representations.

The final `ready` or `failed` state and its audit event commit in one database
transaction.

### Required evidence

Issue #32 must prove:

- admin upload and status success;
- agent `403`, authentication `401`, and tenant-safe `404`;
- strict multipart input and file/type/size/name validation;
- randomized private storage keys;
- storage/database/dispatch compensation paths;
- document/version plus upload-audit atomicity;
- migration round trip and two-tenant constraints.

Issue #33 must prove:

- an ID-only task message;
- PDF/TXT/Markdown extraction;
- scan-before-parse behavior;
- retryable versus permanent failure classification;
- sequential and concurrent duplicate delivery;
- same-task redelivery after worker loss;
- no-op behavior for completed work;
- atomic terminal state plus audit;
- safe response, logs, audit metadata, and Celery result;
- worker stop/start and retry evidence through Docker Compose.

The integrated acceptance path is:

```text
admin upload
  -> 202 + queued
  -> worker extracting
  -> ready or safe failed
  -> status API shows tenant-owned result
  -> duplicate/redelivered task changes nothing
```

Ruff, format, Pyright, the complete PostgreSQL suite, Alembic
upgrade/check/downgrade/upgrade, pre-commit, API/worker Compose smoke, and both
GitHub CI jobs must pass.

## Consequences

- Version-owned state avoids ambiguity when document updates arrive in Week 7.
- Passing a version UUID makes the queue contract future-safe while preserving
  the ID-only rule.
- A local/fake adapter keeps tests deterministic; the interface remains ready for
  private S3 in Week 5.
- Streaming limits and worker-only extraction protect API latency and memory.
- Fail-closed non-local scanning prevents a test fake from silently becoming a
  production security claim.
- Atomic claiming and terminal-state checks make at-least-once Celery delivery
  safe for this workflow.
- Storing extracted text in PostgreSQL is a simple Week 4 handoff to chunking, but
  retention and later large-document behavior must be measured.
- Explicit compensation is more honest than claiming cross-system atomicity, but
  the commit-to-dispatch crash window remains until an outbox or reconciler exists.

## Deferred decisions

- S3/boto3 implementation, bucket provisioning, lifecycle rules, and malware
  service selection;
- transactional outbox or queued-record reconciliation;
- public retry/reprocess and document-version upload endpoints;
- document list/delete/download endpoints;
- connector-sourced documents and external IDs;
- OCR for scanned PDFs;
- password-protected document workflow;
- chunking, embeddings, pgvector, and the `embedding` state;
- content-hash deduplication and active-version rules;
- extraction sandbox hardening beyond the bounded Celery worker process;
- retention, privacy deletion, and real-customer data approval.

## Approval

Emir proposed the contract in `ed974d1`. Eray reviewed that exact commit in PR
#34 on 2026-07-29 and approved the model, upload/storage/scanner boundary, task
state machine, retry/idempotency policy, safe logging rules, required evidence,
and documented distributed-systems residual risk with no blocking findings.

Both contributors therefore accept this ADR. Issues #32 and #33 must implement
the accepted boundary; any material change requires an explicit ADR amendment
and corresponding tests.
