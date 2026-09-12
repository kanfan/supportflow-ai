# SupportFlow AI

[![CI](https://github.com/kanfan/supportflow-ai/actions/workflows/ci.yml/badge.svg)](https://github.com/kanfan/supportflow-ai/actions/workflows/ci.yml)

SupportFlow AI is a learning-focused support copilot for Turkish B2B SaaS teams. It will help support agents prepare faster, source-backed answer drafts from company documentation while keeping a human in control.

> **Project status:** Weeks 1-4 are complete on `main`: authentication, verified
> organization context, admin/agent membership controls, tenant-scoped ticket
> workflows, append-only audit events, the authenticated agent workspace, secure
> document uploads, and retry-safe PDF/TXT/Markdown ingestion. The current
> main CI run at `65275f9` recorded [297 passing tests](https://github.com/kanfan/supportflow-ai/actions/runs/34123283654)
> with PostgreSQL and Redis integration enabled. Week 5 AWS infrastructure and
> pipeline code are in progress; live deployment is currently deferred.
> AI classification and RAG remain follow-up
> milestones.

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

The open-source portfolio goal is a system engineers can run, inspect, test,
and explain. Current work prioritizes reproducible application behavior,
reviewed architecture, regression tests, and measured synthetic evaluation.
AWS remains part of the engineering scope through Terraform, application
adapters, CI, and operational runbooks. Live AWS deployment is deferred;
infrastructure code and offline tests are not deployment evidence.

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
- AWS for the portfolio deployment: Amazon ECR, ECS on Fargate, an Application
  Load Balancer, RDS for PostgreSQL with pgvector, ElastiCache, S3, Secrets
  Manager, and CloudWatch

Runtime and development dependencies are declared in `pyproject.toml` and resolved reproducibly through `uv.lock`.

### AWS deployment target

The AWS environment exists to produce verifiable portfolio evidence, not to
operate a startup or serve real customers. It has no uptime or commercial
availability objective and accepts synthetic data only. A deployment is claimed
only after Terraform apply, GitHub OIDC release, migration, smoke, rollback,
recovery, security, observability, and cost evidence have been recorded. The
environment is then destroyed; it is recreated only for a planned verification
or portfolio-demonstration window.

The API and Celery worker will use the same immutable image from Amazon ECR and
run as separate ECS/Fargate services. Only the Application Load Balancer is
public. ECS tasks have no public IP and use private application subnets with an
initial single NAT Gateway for ECR, logging, secrets, and external-provider
egress. RDS and ElastiCache use isolated data subnets, while private S3 access
uses a gateway endpoint. This single-NAT design is a cost-conscious portfolio
baseline, not a high-availability claim. Uploaded documents use a private,
encrypted, versioned S3 bucket.

GitHub Actions will obtain temporary AWS credentials through OIDC, build and
push a commit-SHA image, run Alembic as a one-off ECS task, deploy the same image
digest to the API and worker, wait for health, and run smoke tests. Secrets are
resolved from AWS Secrets Manager; container logs and alarms use CloudWatch.

Browser session state now uses a Redis-backed adapter with server-side TTLs and
versioned namespaced keys, while tests retain a deterministic in-memory adapter.
Live ElastiCache TLS and API task-replacement evidence remain Week 5 gates. The
complete platform decision and phased implementation plan are documented in
[ADR 0004](./docs/adr/0004-aws-deployment-platform.md) and the
[AWS deployment plan](./docs/aws-deployment-plan.md).

## Current milestone: Week 5 AWS infrastructure and delivery code

The current codebase proves the Week 1-4 application, security, ticket, audit,
agent-workspace, document-upload, and reliable-ingestion foundations. Week 5
moves that existing system toward a reviewable AWS deployment path using the
accepted ownership and handoff contract in
[ADR 0006](./docs/adr/0006-week-5-aws-delivery-and-handoff.md).
The live staging window is deferred; #37/#39 code work can continue while
live #40 verification remains pending. See the
[current portfolio scope](./docs/aws-deployment-plan.md#current-execution-scope).

### Completed baseline

- [x] `docker compose up` starts PostgreSQL/pgvector, Redis, the API, and the worker.
- [x] FastAPI exposes dependency-free `GET /health/live` and dependency-aware `GET /health/ready`.
- [x] A sample Celery task is processed through Redis.
- [x] Automated tests cover the API contract and worker foundation.
- [x] Linting and type checking run successfully.
- [x] Continuous integration runs on pull requests and the main branch.
- [x] Authentication reloads verified organization membership and role context from PostgreSQL.
- [x] Admin/agent RBAC protects organization-membership operations.
- [x] Tenant-scoped tickets and messages support strict status transitions, filtering, and pagination.
- [x] Append-only audit events are integrated atomically with ticket mutations.
- [x] The agent UI rotates server-side sessions and protects state-changing forms with CSRF tokens.
- [x] Week 3 threat, fresh-DB E2E, sensitive-output, and fixed-dataset performance evidence is reproducible.
- [x] Admin document upload and status reads are tenant-scoped.
- [x] PDF, UTF-8 text, and Markdown uploads are streamed, validated, and limited to 10 MiB.
- [x] Document/version state uses private, S3-ready storage and ID-only task boundaries.
- [x] Reliable PDF/TXT/Markdown extraction and retry processing are implemented.
- [x] Duplicate/redelivered document tasks converge on one terminal result and audit event.
- [x] API and worker containers share a private storage volume for local ingestion.
- [x] A claimed `extracting` task survives worker `SIGKILL` and Redis redelivery without duplicate terminal effects.
- [x] The full PostgreSQL/Redis suite and Quality/Container smoke workflows pass
      on `main`; exact-head PR evidence records the current test count.

### Week 5 acceptance targets

- [x] ADR 0006 defines the AWS ownership, credential, apply, handoff, and dependency boundaries.
- [ ] #37 provisions the reviewed AWS platform foundation and publishes sanitized outputs and runbooks.
- [ ] #37's first Terraform slice bootstraps retained remote state, account-wide budget alerts, and scoped GitHub OIDC/IAM roles; no live apply is claimed yet.
- [x] #38 supplies the S3/scanner/session/readiness application adapters.
- [ ] #39 deploys one immutable image digest through scoped GitHub OIDC.
- [ ] #40 records integrated staging, rollback, recovery, security, cost, and teardown evidence.
- [ ] Sanitized deployment evidence is retained and disposable AWS resources
      are destroyed within 24 hours after the planned verification window.

Repository progress as of 2026-09-09:

- [PR #51](https://github.com/kanfan/supportflow-ai/pull/51) merged the #39
  deployment pipeline scaffold; live deployment evidence is still pending.
- [Draft PR #53](https://github.com/kanfan/supportflow-ai/pull/53) adds the first
  #37 Terraform state, budget, and OIDC foundation slice. It is under review;
  no live AWS plan/apply or completed platform handoff is claimed.
- [Draft PR #52](https://github.com/kanfan/supportflow-ai/pull/52) prepares #40's
  verification checklist. Concrete live procedures await the reviewed #37 outputs.

## Week 5 ownership and handoff

Ownership means leading and explaining a feature, not working alone.

| Work package | Lead | Reviewer / pair |
| --- | --- | --- |
| #37 AWS account safety, Terraform state, platform, and applies | Eray | Emir |
| #38 S3/scanner/session/readiness application adapters | Emir | Eray |
| #39 GitHub OIDC and immutable deployment pipeline | Eray | Emir |
| #40 AWS staging technical verification | Emir | Eray |
| Product validation after #40 | Emir | Eray |

The #39 pipeline scaffold is documented in
[AWS deployment pipeline](./docs/aws-deployment-pipeline.md). It is manual and
protected by design: missing #37 environment outputs fail before OIDC is
requested, and no live AWS deployment is claimed until #40 evidence exists.

After ADR 0006 acceptance, #37 platform work, #38 local adapter/contract work,
and #39 pipeline scaffolding can proceed in parallel. Temporary AWS provisioning waits
for the #37 account-safety, budget, state, reviewed-plan, and teardown gates;
live integration waits for sanitized #37 outputs. Eray is the Week 5 Terraform
Apply/Release Captain. Emir does not need AWS root, administrator, or long-lived
credentials and reviews the infrastructure through code, plans, non-secret
outputs, evidence, and runbooks.

## Roadmap

1. **Foundation:** local environment, API skeleton, worker, tests, and CI.
2. **Core support backend:** authentication, organizations, tenant isolation, tickets, and messages.
3. **Document workflow:** secure upload, versioning, extraction, and reliable background processing.
4. **AI classification:** provider adapter, structured output, validation, and evaluation fixtures.
5. **RAG:** chunking, embeddings, tenant-filtered retrieval, citations, and no-answer behavior.
6. **Human approval:** approve, edit, reject, feedback, and audit events.
7. **Reliability showcase (planned):** transactional outbox, Kafka events, idempotent consumers, quarantine/DLQ and replay.
8. **Measured operations (planned):** OpenTelemetry tracing, Prometheus/Grafana, structured logs, lab SLOs and k6 evidence.

The [current roadmap addendum](./docs/distributed-reliability-plan.md) defines
the gated sequence and measurement criteria; [ADR 0008](./docs/adr/0008-distributed-reliability-showcase.md)
is proposed for joint review. Start by closing the existing dispatch crash
window; retain the AI/RAG and human-review milestones. Live AWS deployment is
currently deferred while infrastructure and pipeline code remain in scope.

## Scope boundaries

The first release will not include:

- automatic customer replies;
- microservice extraction or Kubernetes;
- fine-tuning or custom model training;
- complete Zendesk, email, WhatsApp, or call-center integrations;
- billing and subscription management; or
- a complex frontend application.

These boundaries keep the project focused on its main learning and product goals.

Kafka is a planned optional reliability profile under ADR 0008, not a current
runtime dependency. Throughput, latency and recovery claims await measurements.

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
| Readiness | <http://127.0.0.1:8000/health/ready> |
| PostgreSQL/pgvector | `127.0.0.1:5432` |
| Redis | `127.0.0.1:6379` |

`/health/live` reports only whether the API process can answer HTTP. The API
container and future ALB target use `/health/ready`, which requires both a
PostgreSQL `SELECT 1` and Redis `PING`. Dependency failures return only
`{"status":"unavailable"}` with `503`; safe logs carry stable dependency and
error categories. Every HTTP response includes a server-generated
`X-Request-ID` correlation identifier, and caller-supplied values are ignored.

Verify that a task travels through Redis, runs on the worker, and returns its result:

```powershell
docker compose run --rm api python -m scripts.smoke_worker
docker compose run --rm api python -m scripts.smoke_document_ingestion
```

The document smoke creates a synthetic tenant, uploads a Markdown document,
waits for `queued -> extracting -> ready`, delivers the same version task twice
to prove a no-op terminal result, and verifies that a corrupt PDF reaches only a
safe `failed` response. PDF extraction uses `pypdf`; raw files and extracted text
remain outside task arguments, Celery results, audit metadata, and logs.

CI also runs a claimed-job crash test: it blocks the local/test scanner after the
database row reaches `extracting`, kills the worker container, then verifies that
Redis redelivers the unacknowledged task after restart without changing the
attempt count or creating another result/audit event. The dedicated
`compose.worker-loss.yaml` override is smoke-only and cannot be enabled in
staging/production scanner mode.

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

The list endpoint uses `limit`/`offset` pagination, supports `status`,
`source_type`, and `customer_id` filters, and always filters by the verified
organization. Ticket detail and agent mutations are available at:

- `GET /api/v1/tickets/{ticket_id}`;
- `POST /api/v1/tickets/{ticket_id}/messages`;
- `PATCH /api/v1/tickets/{ticket_id}/status`.

The service enforces `open -> processing -> waiting_for_agent -> resolved ->
closed`. Ticket creation, agent messages, and valid status changes commit their
allowlisted audit events in the same transaction. Invalid transitions return the
stable `invalid_ticket_transition` error without changing either table.

### Agent workspace

Open `http://127.0.0.1:8000/ui/login` and sign in with an active user's email,
password, and organization slug. The workspace provides:

- tenant-scoped ticket filters and pagination;
- ticket detail and message timeline;
- agent-authored message and next-status forms;
- safe `403`/`404`/`409` feedback.

The browser never stores a bearer token. Its signed `HttpOnly`, `SameSite=Lax`
cookie contains only an opaque session identifier. Session state and CSRF values
remain server-side; login replaces the pre-authentication session and rotates the
CSRF token, while logout invalidates the server-side session. Staging and
production cookies also use `Secure`.

Outside the test environment, sessions use Redis with a versioned
`supportflow:ui-session` key namespace and server-side TTL. Rotation deletes the
old session and creates the new session in one Redis transaction. Tests use the
same storage contract with a deterministic in-memory adapter, and CI verifies
cross-instance visibility and application replacement against a real Redis
service. Staging and production reject plaintext Redis/Celery URLs and force
certificate plus hostname verification for `rediss://` connections. Redis
connection URLs are secret-valued settings so passwords are omitted from configuration
representations. Live ElastiCache TLS and ECS task-replacement evidence remain
part of the #37 platform handoff and #40 technical verification; #38's local
application scope is complete.

Database URLs are also secret-valued settings. Staging and production require
the `postgresql+psycopg` driver, a database hostname, and an explicit
`SUPPORTFLOW_DATABASE_SSL_ROOT_CERT_PATH`. The shared engine builder forces
`sslmode=verify-full` and passes the trusted CA path to API, worker, readiness,
smoke, and migration connections. Local Compose remains plaintext and
deterministic; live RDS certificate delivery and verification remain #37/#40
evidence.

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

The audit service supplies safe factories used by membership and ticket
mutations. Repositories add rows but never commit independently; the business
service owns the transaction.

### Document upload and ingestion

An authenticated organization `admin` can upload one PDF, UTF-8 text, or
Markdown file with `POST /api/v1/documents`. The API streams the body into a
bounded temporary file, enforces a 10 MiB limit, normalizes the display filename,
checks the extension against the content, and never uses that filename as an
object key.

The object key is generated from tenant, document, and version UUIDs. Local
development stores objects under the private `.supportflow/documents` directory.
The Week 5 `S3DocumentStorage` adapter implements the same `DocumentStorage`
contract with private S3 streaming operations and stable provider-error mapping.
The API and worker select the same adapter through
`SUPPORTFLOW_DOCUMENT_STORAGE_MODE`; staging and production reject local storage.
S3 credentials are never application settings: the SDK uses the ECS task role,
while bucket encryption, public-access blocking, versioning, lifecycle, and IAM
remain #37 platform controls. Responses and logs omit bucket/object identifiers,
hashes, document bodies, extracted text, and internal exception messages.

After storage succeeds, the document, version `1`, and allowlisted
`document.uploaded` audit event commit atomically. Only then does the API publish
the version UUID to Celery. A broker failure leaves an inspectable `failed`
version rather than falsely reporting a queued job. There is still a small crash
window between the database commit and queue publication; ADR 0005 records an
outbox or reconciler as a later reliability decision.

Use the `Location` returned by a successful `202 Accepted` response with
`GET /api/v1/documents/{document_id}`. Both routes are admin-only and use the
verified `X-Organization-ID`; missing and cross-tenant identifiers return the
same `404`.

The concrete ClamD scanner adapter is implemented on `main` and selected with
`SUPPORTFLOW_DOCUMENT_SCANNER_MODE=clamd`. It fails closed and requires the
loopback address `127.0.0.1` outside local/test environments, matching the
accepted worker-sidecar contract. The fake scanner is allowed only for
local/test environments; the `external` configuration label alone is not a
concrete adapter. The S3 adapter has deterministic SDK contract tests.
Worker-side extraction, retry/idempotency behavior, terminal audit atomicity,
and status transitions are also implemented. These complete #38's application
scope; live scanner, bucket/task-role, and AWS integration evidence still require
the #37 platform handoff and #40 verification.

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

The complete AWS-aligned project plan (revision 1.3) is available in
[SupportFlow_AI_Emir_Eray_Proje_Plani_Son_Hal.pdf](./SupportFlow_AI_Emir_Eray_Proje_Plani_Son_Hal.pdf).

### Regenerating the project-plan PDF

The generator is layout-specific: it accepts only the 29-page AWS Revision 1.2
PDF stored at commit `4b869d2`, then produces Revision 1.3. It intentionally
rejects the original 23-page Revision 1.1 PDF before applying any page-indexed
redactions.

Recover the binary source safely from Git in PowerShell:

```powershell
New-Item -ItemType Directory -Force tmp/pdfs | Out-Null
uv run python -c "from pathlib import Path; import subprocess; Path(r'tmp/pdfs/project-plan-revision-1.2.pdf').write_bytes(subprocess.check_output(['git', 'show', '4b869d2:SupportFlow_AI_Emir_Eray_Proje_Plani_Son_Hal.pdf']))"
```

Generate Revision 1.3 without overwriting the recovered source:

```powershell
uv run --with PyMuPDF python scripts/revise_project_plan_for_aws.py tmp/pdfs/project-plan-revision-1.2.pdf SupportFlow_AI_Emir_Eray_Proje_Plani_Son_Hal.pdf
```

The script validates the source page count, metadata title, and first-page
revision marker before redaction. A mismatch raises a `ValueError` that names
the expected revision and recovery commit.

- [Current roadmap addendum: distributed reliability](./docs/distributed-reliability-plan.md) (supersedes the PDF schedule for this extension; planned work)
- [ADR 0008: Distributed reliability showcase](./docs/adr/0008-distributed-reliability-showcase.md) (Proposed)
- [API conventions](./docs/api-conventions.md)
- [ADR 0001: Authentication and organization context](./docs/adr/0001-auth-and-organization-context.md)
- [ADR 0002: Initial data model and API standards](./docs/adr/0002-initial-data-model-and-api-standards.md)
- [ADR 0003: Week 3 security, workflow, audit, and UI contracts](./docs/adr/0003-week-3-security-workflow-and-ui-contracts.md)
- [ADR 0004: AWS deployment platform](./docs/adr/0004-aws-deployment-platform.md)
- [ADR 0005: Document ingestion and worker reliability contracts](./docs/adr/0005-document-ingestion-and-worker-reliability.md)
- [ADR 0006: Week 5 AWS delivery ownership and handoff](./docs/adr/0006-week-5-aws-delivery-and-handoff.md)
- [ADR 0007: Ephemeral AWS portfolio-evidence environment](./docs/adr/0007-ephemeral-aws-portfolio-evidence.md)
- [AWS deployment plan](./docs/aws-deployment-plan.md)
- [Week 5 AWS application handoff contract](./docs/aws-application-handoff-contract.md)
- [AWS platform bootstrap runbook](./docs/runbooks/aws-platform-bootstrap.md)
- [AWS evidence deployment runbook](./docs/runbooks/aws-evidence-deploy.md)
- [AWS restore runbook](./docs/runbooks/aws-restore.md)
- [AWS teardown runbook](./docs/runbooks/aws-destroy.md)
- [Week 3 ticket workflow review](./docs/reviews/week-3-ticket-workflow-and-ui.md)
- [Week 3 threat model](./docs/security/week-3-threat-model.md)
- [Week 3 release verification](./docs/reviews/week-3-verification.md)
- [Week 4 document ingestion worker review](./docs/reviews/week-4-document-ingestion-worker.md)

The plan is a roadmap, not an implementation claim. This README will evolve as working features, tests, measurements, and known limitations are added.

## Data and privacy

The AWS portfolio environment uses synthetic data only, as required by
ADR 0007. A future real-data pilot is outside this deployment scope and requires
a separate decision and privacy review covering data minimization, retention,
deletion, provider policies, access controls, and applicable KVKK obligations.

## Contributors

- Emir
- Eray
