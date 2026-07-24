# ADR 0003: Week 3 security, workflow, audit, and UI contracts

- Status: Accepted
- Date: 2026-07-24
- Tracks: #19
- Prerequisite: #18, merged into `main` as `8a792d3`

## Context

Week 3 joins four concerns that must agree at their boundaries:

- authorization for `admin` and `agent` memberships;
- ticket detail, messages, filters, and status changes;
- append-only audit events;
- a minimal authenticated, server-rendered agent interface.

Implementing these independently would create avoidable security and integration
risks. For example, the API and UI could disagree about roles, a business mutation
could commit without its audit event, or the browser could store bearer tokens in
an unsafe location.

The research-backed discovery baseline in #11 also establishes product constraints:
users want human-approved drafts with source and freshness information, while
tenant isolation, PII handling, and auditability are adoption requirements. Week 3
does not implement answer generation, but it must preserve the security and
traceability needed by that later workflow.

This ADR extends ADR 0001 and ADR 0002. If they conflict, the narrower Week 3
decision in this ADR applies only after both contributors explicitly accept it.

## Decision

### Delivery order and ownership

Week 3 will use the following dependency order:

1. PR #18 completed the tenant-scoped ticket foundation and merged into `main`
   as `8a792d3`.
2. Issue #19 freezes this contract.
3. Emir implements #20, including the only Week 3 migration:
   `0003_audit_events`.
4. Eray rebases #21 onto #20 and integrates ticket mutations with the audit
   service.
5. Both contributors complete the threat model, end-to-end demo, and performance
   baseline in #22. Eray is the Week 3 Integration Captain.

Eray's ticket and UI work does not add a migration unless both contributors first
agree to change migration ownership. This keeps the Alembic history linear and
prevents two branches from independently claiming the revision after
`0002_ticket`.

### Authorization matrix

The authenticated user and active organization membership are reloaded from the
database. A role, user ID, or organization ID supplied by a client is never treated
as authorization.

| Action | `admin` | `agent` |
| --- | --- | --- |
| Select an organization with an active membership | allow | allow |
| List and view tickets in the selected organization | allow | allow |
| Create a manual ticket with an initial message | allow | allow |
| Add an agent-authored ticket message | allow | allow |
| Perform an allowed ticket status transition | allow | allow |
| View the server-rendered ticket workspace | allow | allow |
| List organization memberships | allow | deny |
| Add an existing user to the organization as an agent | allow | deny |
| List tenant audit events | allow | deny |
| Create customer- or system-authored messages through a public endpoint | deny | deny |

Week 3 membership administration is intentionally narrow:

- `GET /api/v1/organization-members` lists memberships for the verified
  `X-Organization-ID` context.
- `POST /api/v1/organization-members` accepts an existing, active user's
  normalized email and the `agent` role.
- Both endpoints are admin-only.
- Invites, creating a user on another person's behalf, removing the final admin,
  and generic role/status management are deferred.
- Adding a membership that already exists returns `409 Conflict`.
- A missing or disabled target user returns the same `404 User not found`
  response, and neither case creates a membership.

The narrow endpoint is sufficient for the two-user Week 3 demo without pretending
that a complete invitation lifecycle exists.

### Authentication and resource-disclosure behavior

The API uses the following stable meanings:

| Status | Meaning |
| --- | --- |
| `401 Unauthorized` | Authentication is missing, invalid, or expired. |
| `403 Forbidden` | The user has an active membership but its role cannot perform the action. |
| `404 Not Found` | The organization, membership, or tenant-owned resource is absent or inaccessible. |
| `409 Conflict` | The request is structurally valid but conflicts with current state. |
| `422 Unprocessable Entity` | The request body, query, or header fails validation. |

Every `401` response preserves `WWW-Authenticate: Bearer`. Cross-tenant lookups
filter by `organization_id` in the database query and return `404`; they do not
first fetch a globally identified resource and then check its organization.

An invalid ticket transition returns this handled-error shape:

```json
{
  "error": {
    "code": "invalid_ticket_transition",
    "message": "The requested ticket status transition is not allowed",
    "details": {
      "current_status": "open",
      "requested_status": "resolved"
    }
  }
}
```

The details contain only safe domain values. Internal exceptions, SQL text,
credentials, and resource data are not returned.

### Ticket workflow boundary

Week 3 exposes these additional tenant-scoped operations:

- ticket detail;
- append an agent-authored message;
- perform one allowed ticket status transition;
- list tickets with deterministic pagination and agreed filters.

The transition graph remains:

```text
open -> processing -> waiting_for_agent -> resolved -> closed
```

Skipped, reverse, and reopen transitions are rejected with `409 Conflict`. A
transition writes the ticket change and its audit event in one transaction. A
failure leaves neither write committed.

The authenticated membership determines `author_user_id` for an agent-authored
message. Public request schemas do not accept another user's ID. Public endpoints
also cannot create `system` messages or claim a customer identity.

Initial filters are limited to fields already closed in ADR 0002:

- `status`;
- `source_type`;
- `customer_id`.

They combine with the mandatory `organization_id` filter. Results retain
`created_at DESC, id DESC` ordering and `limit`/`offset` pagination.

### Audit-event data contract

`audit_events` is append-only and tenant-owned.

| Field | Type and rule |
| --- | --- |
| `id` | Application-generated UUIDv4 primary key |
| `organization_id` | Required UUID; tenant root foreign key |
| `actor_user_id` | Nullable UUID; same-organization membership foreign key |
| `action` | Required lowercase dotted string |
| `resource_type` | Required lowercase string |
| `resource_id` | Nullable UUID |
| `metadata` | Required JSON object, default `{}`, restricted to safe structured values |
| `created_at` | Required `TIMESTAMPTZ`, database default |

The database column remains named `metadata`, but SQLAlchemy Declarative reserves
`metadata` as a Python attribute. The ORM model therefore maps it through a
different attribute, for example
`event_metadata = mapped_column("metadata", JSON, ...)`.

The initial action vocabulary is:

- `organization_member.created`;
- `ticket.created`;
- `ticket_message.created`;
- `ticket.status_changed`.

System actions use a null `actor_user_id`. Human actions derive the actor from the
verified membership; the public API cannot submit it.

Audit rows have no `updated_at`, public update, or public delete operation. The
initial read endpoint is admin-only:

```text
GET /api/v1/audit-events?limit=20&offset=0
```

It uses deterministic `created_at DESC, id DESC` ordering and always filters by
the verified organization.

Audit metadata uses an allowlist per action. It may contain safe values such as
previous/new ticket status or the assigned role. It must not contain:

- passwords or password hashes;
- bearer or session tokens;
- cookies or authorization headers;
- raw ticket-message bodies;
- arbitrary request bodies;
- unnecessary email addresses, phone numbers, tax identifiers, or file contents.

### Atomic audit recording

The application service owns the transaction boundary:

```text
validate -> mutate domain record -> add audit event -> commit
                                      |
                                      +-> any failure rolls back both
```

Repositories add and query records but do not independently commit. This prevents
a successful business change with no audit trail and prevents an audit event from
claiming that a rolled-back change occurred.

### Server-rendered UI authentication

API clients continue to use bearer tokens. The server-rendered UI uses a separate
browser adapter backed by a signed session cookie:

- the cookie is `HttpOnly` and `SameSite=Lax`;
- production-like environments set `Secure`;
- the cookie never contains a password or bearer token;
- the session stores only minimal identifiers and a CSRF value;
- successful login discards all pre-authentication session state, creates a new
  authenticated session, and rotates the CSRF value before setting the cookie;
- the selected organization remains untrusted and is revalidated against an
  active database membership on every request;
- sign-out invalidates the authenticated session state and clears the cookie;
- state-changing HTML forms require a session-bound CSRF token;
- state changes never use `GET`.

The UI login form reuses the existing authentication service instead of
reimplementing password verification. Browser bearer tokens are not stored in
local storage or exposed in URLs.

The initial UI routes are:

- sign in and sign out;
- ticket list with filters and pagination;
- ticket detail with message timeline;
- add-message form;
- status-transition form.

The UI calls the same application services as the API so authorization, tenant
filters, transitions, and audit behavior cannot drift between adapters.

### Threat and verification matrix

| Threat or regression | Required control | Required evidence |
| --- | --- | --- |
| Guessed ticket ID / IDOR | Query by resource ID and verified organization | Two-tenant detail, message, and transition tests return `404` |
| Cross-tenant collection leak | Repository requires `organization_id` | Tenant A list excludes Tenant B records |
| Agent performs admin operation | Role dependency at the endpoint/service boundary | Agent receives `403` from membership and audit endpoints |
| Client impersonates another author | Actor derived from verified membership | Author-ID and system/customer impersonation tests |
| Business mutation lacks audit trail | One service-owned transaction | Success and forced-rollback integration tests |
| CSRF against UI forms | Session-bound token and no state-changing `GET` | Missing/invalid CSRF tests |
| Session or bearer token leakage | `HttpOnly` cookie and safe logs | Cookie-attribute assertion and log scan |
| PII copied into audit metadata | Per-action metadata allowlist | Metadata assertions and secret/PII scan |
| File-upload attack | Uploads remain unsupported in Week 3 | No upload route; future design gate recorded in #22 |

Cross-tenant and role-boundary integration tests are release blockers.

### End-to-end Week 3 acceptance path

The deterministic demo starts from a fresh database:

1. An admin registers and owns Organization A.
2. A second user exists.
3. The admin adds the second user to Organization A as an agent.
4. The agent signs in through the server-rendered UI.
5. The agent lists and opens Organization A tickets.
6. The agent adds a message and performs a valid status transition.
7. An invalid transition is rejected without a partial write.
8. The admin sees the corresponding audit events.
9. The agent cannot access Organization B or its resources.

The same path must be reproducible by automated tests or a documented script, not
only by an informal browser demonstration.

## Consequences

- Admin-only membership and audit endpoints provide a concrete Week 3 role
  boundary and make the two-user demo possible.
- A single migration owner reduces Alembic merge conflicts.
- Service-owned transactions make audit claims reliable but require repositories
  to avoid autonomous commits.
- A separate session adapter adds CSRF responsibilities, but avoids exposing
  bearer tokens to browser storage.
- Returning `404` for inaccessible tenant resources makes debugging slightly less
  explicit while preventing resource enumeration.
- The narrow membership workflow is not a complete invitation product and must not
  be presented as one.

## Deferred decisions

- refresh tokens, token revocation, and production session storage;
- full invitation, role-change, membership-deactivation, and last-admin rules;
- PostgreSQL row-level security;
- ticket reopening or reverse transitions;
- cursor pagination;
- file uploads and malware scanning;
- retention, export, anonymization, and deletion of audit data;
- production latency objectives. Week 3 records a repeatable baseline rather than
  inventing an SLA.

## Approval

Emir and Eray accepted the authorization matrix, transaction boundary, UI session
approach, and migration ownership after reviewing the clarifications in `c7aa28e`
and the merged ticket foundation from PR #18.
