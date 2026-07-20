# SupportFlow AI

SupportFlow AI is a learning-focused support copilot for Turkish B2B SaaS teams. It will help support agents prepare faster, source-backed answer drafts from company documentation while keeping a human in control.

> **Project status:** Planning and Week 1 setup. The product features described below are targets, not completed functionality.

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

Exact dependency versions will be recorded in `pyproject.toml` when the Week 1 application skeleton is created.

## Current milestone: local walking skeleton

The first milestone is deliberately small. It proves that our development environment and collaboration workflow work before we add authentication, business rules, or AI.

### Acceptance criteria

- [ ] `docker compose up` starts the local services.
- [ ] FastAPI exposes `GET /health/live`.
- [ ] A sample Celery task is processed through Redis.
- [ ] At least one automated test passes.
- [ ] Linting and type checking run successfully.
- [ ] Continuous integration runs on the main branch.
- [ ] A second contributor can follow this README on a clean machine.

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

Local setup instructions will be added with the first application skeleton. The intended command will be:

```powershell
docker compose up --build
```

Do not expect this command to work yet. This section will be updated as part of the current milestone.

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

The plan is a roadmap, not an implementation claim. This README will evolve as working features, tests, measurements, and known limitations are added.

## Data and privacy

The portfolio environment will use synthetic or explicitly approved data. A real-data pilot will require a separate privacy review covering data minimization, retention, deletion, provider policies, access controls, and applicable KVKK obligations.

## Contributors

- Emir
- Eray
