# Week 3 Threat Model

Status: release-gate baseline for Issue #22
Scope: identity, organization membership, ticket workflow, audit events, and
the authenticated agent workspace.

## Assets and trust boundaries

The protected assets are tenant tickets and messages, user credentials and
sessions, organization membership and roles, and the append-only audit trail.
Requests cross three relevant boundaries:

1. an untrusted API or browser client enters FastAPI;
2. authenticated identity is combined with an explicit organization context;
3. tenant-scoped application queries and transactions enter PostgreSQL.

Redis currently carries Celery work and is not an authorization source. The
Week 3 browser session store is process-local and is accepted only for the
single-process demo. Shared session storage is a production gate before
horizontal API scaling.

## Threat-to-control map

| Threat | Implemented control | Executable evidence | Residual risk / future gate |
| --- | --- | --- | --- |
| Guessed ticket ID or cross-tenant read/write (IDOR) | Every repository lookup includes verified `organization_id`; inaccessible and missing resources share `404` | `test_ticket_api_detail_filters_messages_transitions_and_audit`, `test_ticket_listing_and_customer_lookup_are_tenant_scoped`, and the Week 3 live E2E | Keep tenant predicates mandatory in every future repository, retrieval, document, and vector query |
| Tenant or role claim forged by a client | JWT identifies only a user; active organization and membership are reloaded from PostgreSQL; admin endpoints use role dependencies | Auth cross-tenant tests, organization-members integration tests, live E2E agent `403` checks | Add policy tests whenever roles or privileged endpoints are introduced |
| Message author impersonation | Message schema forbids actor fields; service derives `author_user_id` from the active membership; database composite FK enforces membership | Week 3 workflow impersonation matrix and ticket model constraint matrix | Customer/system ingestion must get separate trusted adapters, never a client-selectable author type |
| Invalid or partial workflow mutation | Explicit one-step transition graph; domain failure is `409`; business row and allowlisted audit event commit once | Invalid-transition and post-`flush()` rollback integration tests; live E2E verifies unchanged status | Add optimistic concurrency before multiple agents can edit the same ticket concurrently |
| Audit tampering or sensitive payload capture | PostgreSQL blocks update/delete/truncate; metadata uses action-specific factories and allowlists; raw bodies are excluded | Audit append-only, metadata, and rollback tests; Week 3 audit-output scan | Database owners can disable triggers; production database roles, retention, and export controls remain deployment gates |
| Session fixation, CSRF, or logout replay | Login replaces anonymous session and CSRF token; mutations require CSRF; logout invalidates server-side state; cookie is opaque, signed, `HttpOnly`, `SameSite=Lax`, and `Secure` outside local/test | UI session unit tests and combined live UI workflow | Replace process-local storage with shared durable storage before multiple ECS tasks; define forced logout/revocation policy |
| Credential, token, email, or message leakage in logs/evidence | Validation redacts sensitive inputs; audit excludes free text; release script scans logs and audit JSON without echoing matches | `tests/test_week3_sensitive_scan.py` and documented Docker log scan | Production structured logging, field-level redaction, retention, and alerting must be configured in CloudWatch |
| Malicious or oversized document upload | No upload route exists in Week 3; unknown route returns `404` | Live E2E checks the route remains unavailable | Week 4 upload work must add size/type gates, randomized S3 keys, private buckets, malware scanning, and extraction isolation before enablement |

## Security review rule

Engineers should trace each tenant-owned operation in this order:

```text
authenticate user
  -> reload active organization and membership
  -> authorize role
  -> query by organization_id plus resource ID
  -> validate domain transition or input
  -> stage business change and allowlisted audit event
  -> commit exactly once
```

A UUID is an identifier, not authorization. A successful response, database
row, log line, or audit event is not accepted as proof on its own: the release
gate requires the matching negative test and a rollback or isolation check.
