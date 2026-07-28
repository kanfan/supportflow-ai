# Week 3 Release Verification

Estimated reading time: 8 minutes.

## Outcome

Issue #22's release gate is reproducible and green on the measured branch:

- a fresh PostgreSQL database completed the admin-to-agent, API, and browser
  workflow across two tenants;
- negative checks blocked agent administration, cross-tenant ID access, author
  impersonation, invalid transitions, CSRF omission, and session replay;
- all four expected membership/ticket audit actions were present and safe;
- audit JSON plus API/worker container logs passed the credential, JWT, email,
  and exact-canary scan;
- the complete suite passed with **90 automated tests**;
- PostgreSQL, Redis, API, and Celery worker were healthy and the queue
  round-trip passed;
- a fixed **1,000-ticket / 5,000-message** dataset produced a recorded local
  latency and query-plan baseline.

This is an engineering baseline, not a production SLA or concurrent load test.

## How an engineer should think

Release confidence comes from pairing every successful workflow with its
failure boundary:

```text
positive path
  + unauthorized role
  + inaccessible tenant
  + invalid domain action
  + transaction rollback
  + safe evidence output
  + fixed-dataset measurement
```

The [Week 3 threat model](../security/week-3-threat-model.md) maps each threat
to the control and executable evidence. A guessed UUID is treated as untrusted
input, membership is reloaded rather than copied from a token, actor identity
is server-derived, and ticket plus audit writes share one commit.

## Fresh-database E2E evidence

`scripts/week3_e2e.py` ran against a newly created `supportflow_e2e` database at
migration `0003_audit_events`. It creates three users so one user can be added
as an agent to Tenant A while another owns Tenant B.

The script proves:

1. an admin adds an existing active user as an agent;
2. the agent gets `403` from membership and audit administration;
3. Tenant A's list excludes Tenant B's ticket;
4. a Tenant B ticket ID under Tenant A returns the same `404` as a nonexistent
   ID;
5. client-supplied author identity is rejected with `422`;
6. `open -> resolved` returns `409` and leaves the ticket open;
7. browser login rotates the anonymous session and CSRF token;
8. the agent adds a message and performs `open -> processing` through the UI;
9. membership, ticket creation, message creation, and status-change audit
   actions exist;
10. logout invalidates the authenticated session;
11. the unsupported document-upload surface remains closed with `404`.

The machine-readable result is
[`week-3-e2e-evidence.json`](./week-3-e2e-evidence.json). It contains only
pass/fail states and counts—no credentials, tokens, emails, subjects, or message
bodies.

## Sensitive-output verification

`scripts/week3_sensitive_scan.py` reports rule names and line numbers without
printing matched values. It detects bearer authorization headers, compact JWTs,
email addresses, sensitive key/value pairs, and caller-supplied exact canaries.
Three unit tests cover clean allowlisted audit metadata and each detection
class.

The live E2E scanned serialized audit responses for its password, three emails,
three bearer tokens, and both message-body canaries. API and worker container
logs were scanned separately. Both scans passed. This proves the current
scenario; CloudWatch structured redaction and retention remain AWS deployment
work.

## Performance baseline

Environment: Windows 11, Python 3.13.3, Docker PostgreSQL 17.10 over local TCP,
FastAPI TestClient in-process. Each read endpoint received five warmups and each
operation received 40 measured iterations. SQL counts include authentication,
tenant authorization, and the domain operation.

| Operation | Median | p95 | SQL statements |
| --- | ---: | ---: | ---: |
| Filtered ticket list | 18.074 ms | 28.393 ms | 4 |
| Ticket detail + 5 messages | 17.772 ms | 21.846 ms | 4 |
| Agent message creation + audit | 21.402 ms | 30.685 ms | 5 |
| Status transition + audit | 26.782 ms | 38.531 ms | 6 |

The full raw result is
[`week-3-performance-baseline.json`](./week-3-performance-baseline.json).

### Query observations

- Ticket detail uses `uq_tickets_organization_id_id` and returns one row.
- The filtered list uses `ix_tickets_organization_created_id`, returns 20
  rows, and recorded 115 shared-hit blocks. The current scale is healthy, but
  a larger/status-heavy dataset should re-evaluate an index beginning with
  `(organization_id, status, created_at, id)`.
- The five-message timeline uses
  `ix_ticket_messages_organization_ticket_created`, then performs a small
  in-memory sort. Re-evaluate adding `id` to the ordering index only after a
  larger per-ticket message distribution demonstrates a real cost.
- The stable statement counts provide a regression signal. A future PR that
  increases list/detail query count should explain why.

## Build map

- `docs/security/week-3-threat-model.md` owns threat/control/test/future-gate
  traceability.
- `scripts/week3_e2e.py` owns the repeatable live release scenario and emits
  sanitized JSON evidence.
- `scripts/week3_sensitive_scan.py` owns safe evidence/log scanning.
- `scripts/week3_performance_baseline.py` safety-checks the exact disposable
  database name, resets migrations, seeds deterministic data, measures HTTP
  operations and SQL counts, and records `EXPLAIN (ANALYZE, BUFFERS)` summaries.
- `tests/test_week3_sensitive_scan.py` prevents the scanner from silently
  accepting supported leak shapes.
- The two JSON files preserve exact evidence without requiring claims to be
  copied by hand.

No migration or runtime API behavior changed in this release-verification
slice.

## Reproduction

Run database-resetting commands sequentially; two test processes must never
share `supportflow_test` or `supportflow_perf`.

```powershell
docker compose up --detach postgres
docker compose exec postgres createdb --username supportflow supportflow_test
docker compose exec postgres createdb --username supportflow supportflow_perf

$env:SUPPORTFLOW_TEST_DATABASE_URL = "postgresql+psycopg://supportflow:supportflow@127.0.0.1:5432/supportflow_test"
uv run pytest

$env:SUPPORTFLOW_PERF_DATABASE_URL = "postgresql+psycopg://supportflow:supportflow@127.0.0.1:5432/supportflow_perf"
uv run python -m scripts.week3_performance_baseline `
  --output docs/reviews/week-3-performance-baseline.json
```

The performance runner refuses any database whose exact name is not
`supportflow_perf`. The E2E runner expects an already migrated, clean API:

```powershell
uv run python -m scripts.week3_e2e `
  --base-url http://127.0.0.1:8000 `
  --output docs/reviews/week-3-e2e-evidence.json

docker compose logs --no-color api worker |
  uv run python -m scripts.week3_sensitive_scan
```

The full Compose build, API health check, Alembic check, worker queue
round-trip, Ruff, formatting, Pyright, and pre-commit checks also passed.
