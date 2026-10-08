# A1b fake-only application integration

Issue #59 remains open. Emir implements; Eray reviews. Design PR #64 was approved
at `3f09c2a` and merged as `c8d331d`. This slice claims no real-model quality,
provider integration, token/cost measurement, customer-data rollout or paid call.

## Scope and use

Migration `0007_classification` adds tenant-scoped operations with ticket and
requesting-actor membership composite foreign keys. A result preserves exact
opening-input identity, versions and a configuration digest including the fake
script hash. The versioned canonical hash tags response/error scripts separately
and includes the error's qualified type and category. Switching from an error to
an equal raw response string invalidates reuse. This hash-format correction
changes earlier fake configuration digests; existing rows remain historical and
read as stale, never rewritten in place. No ticket body, subject, prompt text or
raw response is copied.

The two ADMIN/AGENT routes are `GET` and `POST`
`/api/v1/tickets/{ticket_id}/classification`. They use the existing bearer token
and verified organization context. POST accepts no configuration/input overrides
(no body or an empty object); GET never invokes a provider or mutates a claim.
Only a `current` envelope includes the strict result. Errors use safe codes.

Normal `create_app()` has no classification runtime, so POST returns 503
`classification_disabled` after tenant/ticket checks. There is no environment
switch, SDK, credential setting, network transport or real mode. For isolated
synthetic tests/local demonstrations, explicitly inject:

```python
from app.classification.application import FakeClassificationRuntime
from app.classification.providers import FakeClassificationProvider
from app.config import Settings
from app.main import create_app

fake = FakeClassificationProvider("demo", {
    "demo": '{"outcome":"classified","category":"technical_issue"}',
})
app = create_app(Settings(environment="test"),
                 classification_runtime=FakeClassificationRuntime(fake))
```

That script is deliberately constant; it does not classify arbitrary ticket
meaning. Never use fixture IDs as production-ticket routing. Injection is rejected
outside local/test. Configuration versions are server-owned. Token/cost values
remain null; elapsed time measures fake execution, not external-model latency.

## Transactions, freshness and failure semantics

The route copies authorized IDs and rolls back the dependency's auth transaction.
The service owns separate short sessions: authorize/lock/select/claim and commit,
then call the fake with an immutable snapshot and no checked-out DB connection,
then reauthorize/lock/recheck/save result and audit atomically. Authorization rows
are share-locked during these transactions; ticket input writers use the same
ticket row lock. The existing append-message path now participates. Any future
subject/body editing path must also take that lock before modifying input.

The database uniqueness rule covers the entire tenant/ticket/input/config
identity, not only active claims. Successful requests reuse their result.
Concurrent, failed and unknown requests never cause an automatic second call for
that identity. This conservative fake slice has no retry/rearm endpoint. Changed
input or configuration creates a new identity; unchanged later messages do not.

Claims have a 30-second expiry. GET projects an expired in-progress claim as
unknown without rewriting it; POST can materialize that unknown state and its
audit event, but cannot re-dispatch. A late completion cannot resurrect it.
Timeout/unavailable/unexpected exceptions are uncertain; authentication,
rate-limit and invalid-output errors are terminal safe failures. Changed input
discards the suggestion; revoked access discards it and returns forbidden.
Failure to persist the result/audit leaves the committed claim blocking repeats.
This is not an exactly-once billing guarantee. The immediate scripted fake has no
network I/O; a future real adapter needs its separately reviewed transport-level
deadline and budget enforcement, not just this claim expiry.

## Operator reconciliation and retention

There is no automated expiration worker or public force/retry API. Inspect the
tenant-scoped operation ID, timestamps, safe status, input/config hashes and audit
record with authorized DB access; do not export input text or credentials. Retain
unknown evidence. For this fake-only slice, record that no external transport
exists; do not relabel an unknown result as succeeded. Any exceptional database
repair needs a reviewed, tenant-scoped procedure and retained evidence. A new
synthetic test ticket/configuration is not reconciliation of the old claim.

Before any future real-provider release/retry, the separate design must supply
usage reconciliation and reservations; unknown usage cannot be treated as free.
No such operator action, real provider or budget ledger is implemented here.
Downgrade refuses while classification rows remain, preventing accidental loss
of unresolved claims or reviewed evidence. Empty-schema downgrade/upgrade is
covered by the existing disposable PostgreSQL migration fixture. Archiving and
deleting retained evidence are explicit operator decisions, not automatic cleanup.

## Review and verification

New tests cover model/runtime guards, both API routes, tenant/actor FKs, database
result constraints, active membership and user/organization state, stale input
and configuration, local shortcuts, oversized input, safe provider errors,
concurrent claims, expiry/late completion, no checked-out connections during I/O,
append locking, audit rollback and downgrade protection. Audit rollback tests
flush both completion updates and the real audit insert, verify their values
with SQL inside the transaction, then inject failure. Fresh sessions prove both
writes rolled back for successful completion and terminal provider errors.

```powershell
uv run pytest tests/test_classification_application_contract.py -q
# Requires a disposable PostgreSQL database named supportflow_test:
uv run pytest tests/integration/test_classification_application.py -q
uv run ruff check .
uv run ruff format --check .
uv run pyright
```

Use PR CI for full PostgreSQL, migration and container evidence. Local unit tests
alone do not establish transaction/concurrency correctness. Review implementation
evidence before checking the application acceptance item on #59. Real-provider
selection, request/token/spend caps and quality thresholds remain unapproved;
effective external-request and spend authorization is zero.
