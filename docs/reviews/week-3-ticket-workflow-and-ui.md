# Week 3 Review: Ticket Workflow and Agent UI

Estimated reading time: 8 minutes.

## Purpose

This slice turns the Week 2 ticket foundation and Emir's audit foundation into a
usable, tenant-safe workflow. An authenticated admin or agent can list and open
tickets, add an agent message, and advance the allowed status graph through the
API or the server-rendered workspace. Every successful ticket mutation records
an audit event in the same PostgreSQL transaction.

The feature deliberately does not add a migration. It builds on
`0003_audit_events` and preserves Emir's ownership of the Week 3 schema history.

## How to think about the workflow

Use this order for every tenant-owned operation:

```text
authenticate
  -> reload active user, organization, and membership
  -> query the resource with organization_id
  -> validate the domain rule
  -> stage the ticket/message change
  -> stage allowlisted audit metadata
  -> commit once
```

Authorization is not a property of a UUID supplied by a client. The verified
organization context must be part of the database query. A missing resource and
a resource in another tenant therefore produce the same `404`.

Audit recording is not logging after a successful commit. It is part of the
business transaction. If audit creation fails, the ticket mutation must roll
back. If a transition is invalid, neither a database change nor an audit claim
may exist.

## Build map

### Ticket API and domain

- `app/api/tickets.py` exposes detail, filtered list, agent-message, and
  status-transition endpoints. It translates domain failures into the stable
  API error envelope.
- `app/tickets/schemas.py` accepts only the fields a client is allowed to
  control. Message requests have a body but no actor, customer, or system
  identity.
- `app/tickets/repository.py` requires `organization_id` and applies it to
  detail, messages, filtered items, and the filtered total count.
- `app/tickets/service.py` owns validation and transaction boundaries. Ticket
  creation records `ticket.created` plus `ticket_message.created`; later
  messages and transitions record their matching audit actions.
- `app/errors.py` supports safe domain-specific error codes such as
  `invalid_ticket_transition` without exposing exception or SQL details.

### Browser workspace

- `app/ui/session.py` signs an opaque session identifier and keeps user,
  organization, expiry, and CSRF state on the server.
- `app/ui/router.py` reloads active identity and membership state on every
  request, validates CSRF on every state-changing form, and calls the same
  ticket service used by the API.
- `app/ui/templates/` contains the login, filtered list, detail timeline,
  message/status forms, and safe error page.
- `app/main.py` creates the UI session store and registers the UI router.
- `app/config.py` bounds the configurable UI session lifetime.

The API continues to use bearer JWTs. The browser adapter does not store a
bearer token in cookies, URLs, HTML, or local storage.

The current server-side store is process-local for the Week 3 single-process
demo. Horizontal scaling requires shared, durable session storage; ADR 0003
deliberately defers that production decision.

## Why the tests exist

`tests/integration/test_week3_ticket_workflow.py` proves the security contracts:

- combined status/source/customer filters keep their tenant scope and total;
- guessed cross-tenant IDs return `404` for detail, message, and status paths;
- message schemas reject user, customer, and author-type impersonation fields;
- an invalid status jump returns `409` and creates no audit event;
- successful create, message, and transition operations create safe audit rows;
- an audit failure rolls back the pending agent message;
- login replaces the anonymous session and rotates CSRF;
- replaying the old login cookie or a logged-out cookie cannot restore access;
- missing CSRF blocks UI mutations;
- cookies are `HttpOnly`, `SameSite=Lax`, and `Secure` in production-like
  environments.

`tests/test_ui_session.py` isolates signing, tamper detection, expiry, rotation,
and invalidation. Existing ticket integration tests remain as regression proof
for the Week 2 database constraints and transition graph.

Tests should be read as executable threat controls, not as line coverage.

## Verification workflow

Before review:

1. Run Ruff format/lint and Pyright.
2. Run the complete suite against the disposable `supportflow_test` database.
3. Run Alembic upgrade/check/downgrade/upgrade.
4. Build the Compose images and wait for PostgreSQL, Redis, API, and worker
   health.
5. Verify the API and Celery queue round trip.
6. Open login, ticket list, and detail in desktop and mobile viewports and check
   browser console errors.

The UI was visually checked at desktop and narrow mobile widths with no
horizontal overflow or browser errors.

## Week 3 status

After this slice:

- ADR/security contracts, membership/RBAC, audit foundation, ticket workflow,
  and authenticated agent UI are implemented.
- Issue #22 was the remaining Week 3 release gate at the time of this review.

The follow-up [Week 3 release verification](./week-3-verification.md) now records
the fresh-database admin-to-agent scenario, joint threat model, log/audit
sensitive-output scan, and fixed-dataset median/p95 performance baseline.
