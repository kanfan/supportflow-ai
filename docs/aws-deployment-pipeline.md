# AWS deployment pipeline contract

Issue #39 owns the repeatable release path from GitHub to the short-lived AWS
portfolio environment. It is intentionally a manual, protected workflow. A
green workflow run is pipeline evidence; it is not by itself a claim that AWS
staging has been deployed. That claim is made only after the integrated #40
evidence and teardown record exists.

## Workflow entry point

The workflow is `.github/workflows/aws-deploy.yml` and is started with
`workflow_dispatch` from a protected `staging` or `production-demo` GitHub
environment. It accepts these operations:

- `deploy`: build, migrate, promote, readiness-smoke, and log-scan one release;
- `rollback`: restore two previously compatible API/worker task-definition
  revisions and rerun readiness/application smoke without a database downgrade.

The workflow calls the existing CI workflow through `workflow_call`, so Ruff,
formatting, Pyright, the PostgreSQL/Redis suite, migration round-trip, and
Container smoke are required before any deployment job can start. Pull-request
workflows have only `contents: read`; only the protected deploy/rollback job
has `id-token: write`.

## OIDC and environment handoff

The #37 handoff supplies non-secret GitHub environment variables. The workflow
validates all of these before requesting AWS credentials:

| Variable | Purpose |
| --- | --- |
| `AWS_REGION` | Approved AWS region |
| `SUPPORTFLOW_AWS_ROLE_ARN` | Scoped GitHub OIDC deploy role ARN |
| `SUPPORTFLOW_ECR_REPOSITORY` | ECR repository name |
| `SUPPORTFLOW_ECS_CLUSTER` | ECS cluster name |
| `SUPPORTFLOW_ECS_API_SERVICE` / `SUPPORTFLOW_ECS_WORKER_SERVICE` | Service names |
| `SUPPORTFLOW_ECS_API_TASK_FAMILY` / `SUPPORTFLOW_ECS_WORKER_TASK_FAMILY` / `SUPPORTFLOW_ECS_MIGRATION_TASK_FAMILY` | Pre-provisioned task-definition families |
| `SUPPORTFLOW_ECS_*_CONTAINER_NAME` | Application container names to replace |
| `SUPPORTFLOW_ECS_SUBNET_IDS` / `SUPPORTFLOW_ECS_SECURITY_GROUP_IDS` | Private Fargate network |
| `SUPPORTFLOW_ALB_BASE_URL` | HTTPS ALB origin |
| `SUPPORTFLOW_CLOUDWATCH_LOG_GROUP` | Release-log scan target |
| `SUPPORTFLOW_MIGRATION_BACKUP_CONFIRMED` | Human/platform backup gate, exactly `true` |

The role ARN is an identifier, not an access key. No `AWS_ACCESS_KEY_ID`,
`AWS_SECRET_ACCESS_KEY`, database password, Redis URL, or secret value belongs
in repository or GitHub variables. Application credentials are injected by the
ECS task definition through Secrets Manager references supplied by #37.

The synthetic smoke account is supplied through protected environment secrets:
`SUPPORTFLOW_SMOKE_ORGANIZATION_ID`, `SUPPORTFLOW_SMOKE_EMAIL`,
`SUPPORTFLOW_SMOKE_PASSWORD`, `SUPPORTFLOW_SMOKE_INITIAL_BODY`, and
`SUPPORTFLOW_SMOKE_FOLLOWUP_BODY`. The smoke runner never prints these values.

## Immutable release sequence

1. Required CI completes.
2. The OIDC role is assumed with a run-specific session name.
3. Docker builds one image tagged with the full commit SHA and pushes it to ECR.
4. The workflow resolves the ECR `sha256:` digest and uses only the resulting
   `repository@sha256:...` URI from this point onward.
5. The existing API, worker, and migration task definitions are fetched from
   ECS. The pure `scripts/aws/release_contract.py` helper replaces exactly one
   named application container image and strips AWS read-only registration
   fields. ClamD sidecar images and all other task settings remain unchanged.
6. The migration task is registered and run with the private Fargate network.
   A non-zero exit stops promotion; no service is updated. The normal rollback
   path never runs `alembic downgrade`.
7. API and worker task definitions are registered with the same digest, both
   services are updated, and ECS stability is required.
8. ALB readiness and the synthetic authentication, ticket, follow-up message,
   document upload, and worker-ingestion smoke run against the deployed origin.
9. Recent CloudWatch messages are scanned without echoing them. Known password
   and message canaries are passed as forbidden values to the existing
   sensitive-output scanner.
10. Only an allowlisted evidence JSON is uploaded: commit SHA, image digest,
    environment, migration revision, task-definition family/revisions, and
    smoke status. It contains no registry URL, account secret, endpoint secret,
    token, filename, body, or extracted text.

## Concurrency and rollback

Every deployment and rollback uses this exact group:

```text
supportflow-aws-<environment>
```

`cancel-in-progress: false` ensures a second operation waits instead of
interrupting the first. The future #40 destroy/teardown workflow must reuse the
same group and protected environment. It must not run Terraform destroy while a
release or rollback is active.

Rollback requires explicit previous API and worker `family:revision` inputs.
The workflow verifies each family before updating services, waits for stability,
and reruns readiness/application smoke. It does not change the database schema;
schema recovery remains the reviewed RDS restore/forward-fix procedure.

## Evidence boundary

This workflow is a release mechanism, not the whole AWS acceptance process. #40
still has to prove private S3/task-role access, live ClamD sidecar freshness,
RDS CA/hostname verification, ElastiCache TLS and task replacement, rollback,
restore, CloudWatch redaction, cost thresholds, and destroy within 24 hours.
