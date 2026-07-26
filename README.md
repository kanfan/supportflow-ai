# SupportFlow AI

[![CI](https://github.com/kanfan/supportflow-ai/actions/workflows/ci.yml/badge.svg)](https://github.com/kanfan/supportflow-ai/actions/workflows/ci.yml)

SupportFlow AI is a learning-focused support copilot for Turkish B2B SaaS teams. It will help support agents prepare faster, source-backed answer drafts from company documentation while keeping a human in control.

> **Project status:** The foundation and core support backend are complete on
> `main`: authentication, verified organization context, admin/agent membership
> controls, and tenant-scoped ticket and message workflows. Audit events are in
> progress. Document ingestion and AI/RAG remain planned work.

## The problem

Support agents often search through product documentation and past solutions manually. This takes time, makes onboarding harder, and can produce inconsistent answers. General-purpose chatbots also create a trust problem when they answer without showing where the information came from.

SupportFlow AI is designed to:

- classify incoming support tickets;
- retrieve relevant information from company documents;
- generate an answer draft with verifiable citations;
- return an insufficient-context result instead of inventing an answer; and
- require an agent to approve, edit, or reject every suggestion.

The first target users are small and medium-sized Turkish B2B SaaS support teams with documentation-heavy products.

## Why we are building it

This project is being developed by Emir and Eray as a practical, end-to-end learning project after graduation. Both contributors will work across backend development, databases, asynchronous processing, AI/RAG, testing, security, observability, and deployment.

We will rotate feature ownership rather than permanently dividing the project into “backend” and “AI” roles. The goal is for both contributors to understand and explain the complete system.

## Product principles

1. **Sources before confidence:** an answer is useful only when its claims can be checked against retrieved sources.
2. **No answer is better than an invented answer:** insufficient context is a valid product result.
3. **Humans remain in control:** v1 will never send an AI-generated response to a customer automatically.
4. **Tenant isolation is mandatory:** one organization must never access another organization’s tickets, documents, or retrieved chunks.
5. **Synthetic data by default:** the portfolio release will not use real customer tickets or personal data.
6. **Measure quality:** retrieval, citations, no-answer behavior, latency, cost, and agent decisions will be evaluated.

## Planned architecture

SupportFlow AI will begin as a modular monolith. The API and background worker will share one codebase while domain modules remain separated by clear boundaries.

```text
Agent UI
   |
FastAPI API ------> PostgreSQL + pgvector
   |
Redis queue ------> Celery worker
                         |--- document ingestion
                         |--- ticket classification
                         |--- retrieval and generation
                         |--- LLM and embedding adapters
```

### Planned technology stack

- Python and FastAPI
- Pydantic
- PostgreSQL and pgvector
- SQLAlchemy and Alembic
- Redis and Celery
- pytest, Ruff, and Pyright
- Docker and Docker Compose
- GitHub Actions
- Render for the portfolio deployment

Runtime and development dependencies are declared in `pyproject.toml` and resolved reproducibly through `uv.lock`.

## Current milestone: Week 3 security and workflow foundation

The current `main` branch proves the local development foundation,
authentication and role boundaries, organization isolation, and the first
end-to-end ticket workflow. The next security layer adds append-only audit
events before document ingestion and AI/RAG work begins.

### Acceptance criteria

- [x] `docker compose up` starts PostgreSQL/pgvector, Redis, the API, and the worker.
- [x] FastAPI exposes `GET /health/live`.
- [x] A sample Celery task is processed through Redis.
- [x] Automated tests cover the API contract and worker foundation.
- [x] Linting and type checking run successfully.
- [x] Continuous integration runs on pull requests and the main branch.
- [x] Authentication reloads verified organization membership and role context from PostgreSQL.
- [x] Admin/agent RBAC protects organization-membership operations.
- [x] Tenant-scoped tickets and messages support strict status transitions, filtering, and pagination.
- [ ] Append-only audit events are integrated with ticket mutations.
- [ ] Document ingestion and AI/RAG are implemented.

## Initial ownership

Ownership means leading and explaining a feature, not working alone.

| Area | Initial lead | Reviewer / pair |
| --- | --- | --- |
| Repository and FastAPI skeleton | Emir | Eray |
| PostgreSQL, Redis, and Docker Compose | Eray | Emir |
| Testing and initial CI | Eray | Emir |
| API conventions and error format | Emir | Eray |
| Integration and weekly demonstration | Shared | Shared |

Feature leadership will rotate as the project progresses.

## Roadmap

1. **Foundation:** local environment, API skeleton, worker, tests, and CI.
2. **Core support backend:** authentication, organizations, tenant isolation, tickets, and messages.
3. **Document workflow:** secure upload, versioning, extraction, and reliable background processing.
4. **AI classification:** provider adapter, structured output, validation, and evaluation fixtures.
5. **RAG:** chunking, embeddings, tenant-filtered retrieval, citations, and no-answer behavior.
6. **Human approval:** approve, edit, reject, feedback, and audit events.
7. **Production-like quality:** observability, security testing, deployment, evaluation, and portfolio documentation.

## Scope boundaries

The first release will not include:

- automatic customer replies;
- microservices, Kubernetes, Kafka, or a separate event bus;
- fine-tuning or custom model training;
- complete Zendesk, email, WhatsApp, or call-center integrations;
- billing and subscription management; or
- a complex frontend application.

These boundaries keep the project focused on its main learning and product goals.

## Local development

### Prerequisites

- Git
- Docker Desktop or Docker Engine with Compose v2
- Optional for host-based development: Python 3.13 and [uv](https://docs.astral.sh/uv/getting-started/installation/)

### Start the full stack

Copy the example environment file once:

```powershell
Copy-Item .env.example .env
```

Build the application image and wait for all four services to become healthy:

```powershell
docker compose up --detach --build --wait
docker compose run --rm api alembic upgrade head
```

Available local endpoints and services:

| Service | Address |
| --- | --- |
| API | <http://127.0.0.1:8000> |
| OpenAPI UI | <http://127.0.0.1:8000/docs> |
| Liveness | <http://127.0.0.1:8000/health/live> |
| PostgreSQL/pgvector | `127.0.0.1:5432` |
| Redis | `127.0.0.1:6379` |

Verify that a task travels through Redis, runs on the worker, and returns its result:

```powershell
docker compose run --rm api python -m scripts.smoke_worker
```

Inspect logs or stop the stack:

```powershell
docker compose logs --follow api worker
docker compose down
```

`docker compose down --volumes` also deletes local PostgreSQL and Redis data. Use it only when a clean reset is intended.

### Run the API and worker on the host

Keep PostgreSQL and Redis in Docker, install the locked Python environment, and start each process in a separate terminal:

```powershell
docker compose up --detach postgres redis
uv sync --locked --all-groups
uv run fastapi dev app/main.py
uv run celery -A app.worker.celery_app worker --loglevel=INFO
```

Settings use the `SUPPORTFLOW_` prefix and are documented in `.env.example`.

### Authentication flow

Registration creates the first user, organization, and `admin` membership in one
database transaction. Passwords are stored only as Argon2id hashes. Access tokens
identify the user for 15 minutes; organization access and role are always reloaded
from PostgreSQL.

Register and log in from PowerShell:

```powershell
$registration = @{
  email = "admin@example.com"
  password = "correct horse battery staple"
  organization_name = "Example Company"
  organization_slug = "example-company"
} | ConvertTo-Json

$registered = Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8000/api/v1/auth/register `
  -ContentType "application/json" `
  -Body $registration

$login = @{
  email = "admin@example.com"
  password = "correct horse battery staple"
} | ConvertTo-Json

$token = Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8000/api/v1/auth/login `
  -ContentType "application/json" `
  -Body $login

Invoke-RestMethod -Method Get `
  -Uri http://127.0.0.1:8000/api/v1/auth/me `
  -Headers @{ Authorization = "Bearer $($token.access_token)" }
```

Tenant-owned endpoints additionally require `X-Organization-ID`. The API verifies
that header against the authenticated user's active membership; it never trusts an
organization or role supplied inside a token.

### Ticket flow

Ticket creation requires an initial message and writes both records in one database
transaction. The authenticated membership supplies the agent identity; clients
cannot submit another `author_user_id`.

Continue the authentication example above:

```powershell
$tenantHeaders = @{
  Authorization = "Bearer $($token.access_token)"
  "X-Organization-ID" = $registered.organization.id
}

$ticketRequest = @{
  subject = "Unable to export a report"
  initial_message = @{
    author_type = "agent"
    body = "Customer reported a reproducible export problem."
  }
} | ConvertTo-Json -Depth 3

Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8000/api/v1/tickets `
  -Headers $tenantHeaders `
  -ContentType "application/json" `
  -Body $ticketRequest

Invoke-RestMethod -Method Get `
  -Uri http://127.0.0.1:8000/api/v1/tickets `
  -Headers $tenantHeaders
```

The list endpoint uses `limit`/`offset` pagination and always filters by the verified
organization. Ticket status transitions are currently enforced in the service layer
as `open -> processing -> waiting_for_agent -> resolved -> closed`.

### Membership and audit flow

An authenticated organization `admin` can list memberships and add an existing,
active user as an `agent` through `/api/v1/organization-members`. An `agent`
receives `403 Forbidden` from this administration surface.

Membership creation and its `organization_member.created` audit event commit in
the same database transaction. If either write fails, both are rolled back. Audit
rows are tenant-scoped, append-only, and expose only action-specific metadata
allowlists—never passwords, tokens, authorization headers, raw message bodies, or
arbitrary request payloads.

PostgreSQL rejects normal `UPDATE`, `DELETE`, and `TRUNCATE` operations on the
audit table. This protects against application-role mistakes; it does not claim to
protect against a privileged database owner who can disable or remove triggers.

Admins can read their selected organization's audit timeline with deterministic
pagination:

```powershell
Invoke-RestMethod -Method Get `
  -Uri "http://127.0.0.1:8000/api/v1/audit-events?limit=20&offset=0" `
  -Headers $tenantHeaders
```

The audit service also defines the safe interfaces used by later ticket creation,
message, and status-transition integration. Repositories add rows but never commit
independently; the business service owns the transaction.

### Database migrations

Alembic migrations are the version history for the PostgreSQL schema. Apply every
pending migration before running code that depends on new tables:

```powershell
uv run alembic upgrade head
uv run alembic check
```

`alembic check` detects model changes for which a migration has not been written.
To test a complete migration round trip on a disposable database:

```powershell
uv run alembic downgrade base
uv run alembic upgrade head
```

The downgrade command removes the migrated tables and their data. Never run it
against a database whose data you need to keep.

### PostgreSQL integration tests

The normal test command skips database integration tests unless an explicit,
disposable database named `supportflow_test` is configured. With the Compose
PostgreSQL service running, create it once:

```powershell
docker compose up --detach postgres
docker compose exec postgres createdb --username supportflow supportflow_test
$env:SUPPORTFLOW_TEST_DATABASE_URL = "postgresql+psycopg://supportflow:supportflow@127.0.0.1:5432/supportflow_test"
uv run pytest -m integration
```

If `createdb` reports that the database already exists, continue with the next
command. The integration suite verifies upgrade/check/downgrade/upgrade and leaves
the disposable schema at Alembic `base` when it finishes.

### Quality checks

Run the same checks as CI before opening a pull request:

```powershell
uv lock --check
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest
uv run pre-commit run --all-files
```

Install the Git hook once with `uv run pre-commit install`. GitHub Actions repeats these checks and also builds the containers, waits for every service health check, calls the API, and performs the Celery queue round trip.

## Working agreement

- Use short-lived feature branches such as `feature/health-endpoint`.
- Do not push directly to `main` after branch protection is enabled.
- Require one review and passing CI before merging.
- Keep pull requests small enough to understand and test.
- Pair on security-sensitive, tenant-isolation, and RAG decisions.
- Demonstrate the integrated system every Friday.
- Record important technical decisions in the repository.
- Ask “why?” during reviews; code ownership is shared after merge.

## Documentation

The complete project plan is available in [SupportFlow_AI_Emir_Eray_Proje_Plani_Son_Hal.pdf](./SupportFlow_AI_Emir_Eray_Proje_Plani_Son_Hal.pdf).

- [API conventions](./docs/api-conventions.md)
- [ADR 0001: Authentication and organization context](./docs/adr/0001-auth-and-organization-context.md)
- [ADR 0002: Initial data model and API standards](./docs/adr/0002-initial-data-model-and-api-standards.md)
- [ADR 0003: Week 3 security, workflow, audit, and UI contracts](./docs/adr/0003-week-3-security-workflow-and-ui-contracts.md)

The plan is a roadmap, not an implementation claim. This README will evolve as working features, tests, measurements, and known limitations are added.

## Data and privacy

The portfolio environment will use synthetic or explicitly approved data. A real-data pilot will require a separate privacy review covering data minimization, retention, deletion, provider policies, access controls, and applicable KVKK obligations.

## Contributors

- Emir
- Eray
