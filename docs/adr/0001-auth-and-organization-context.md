# ADR 0001: Authentication and organization context

- Status: Accepted for the initial implementation
- Date: 2026-07-21

## Context

SupportFlow is multi-tenant. A user may eventually belong to more than one
organization, and every tenant-owned query must be scoped before data is returned.

## Decision

- Passwords will be hashed with Argon2id; plaintext passwords are never stored or
  logged.
- API authentication will use short-lived bearer access tokens. Refresh-token and
  revocation behavior will be decided before public deployment.
- Tenant endpoints will require an explicit organization selector. The selector is
  untrusted input and is accepted only after the authenticated user's active
  membership is verified.
- Application services and repositories receive the verified organization ID
  explicitly. Repository methods for tenant-owned data cannot omit it.
- Authorization is based on the verified membership role (`admin` or `agent`), not
  on a role value supplied by the client.
- Background tasks reload both the entity and its organization relationship from the
  database instead of trusting identifiers from the queue message.

## Consequences

- Tokens identify the user but do not grant access to every organization named by a
  request.
- Cross-tenant integration tests are release blockers.
- A future move from bearer tokens to secure browser sessions can replace the HTTP
  authentication adapter without changing repository tenant rules.
