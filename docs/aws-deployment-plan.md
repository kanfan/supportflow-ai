# SupportFlow AWS deployment plan

This document turns ADR 0004 into a staged engineering plan. It defines the
order and evidence; it does not claim that AWS infrastructure already exists.

## Target topology

```text
Internet
   |
Route 53 / ACM
   |
Application Load Balancer (public subnets)
   |
ECS/Fargate API service (private subnets)
   |--------- RDS PostgreSQL + pgvector (private)
   |--------- ElastiCache Valkey/Redis OSS (private, TLS)
   |--------- S3 gateway endpoint --> private S3 document bucket
   |--------- NAT Gateway --> ECR, CloudWatch Logs, Secrets Manager
   |                       \-> external LLM/provider HTTPS APIs
   |
ECS/Fargate Celery worker (private subnets)

ECR image ----> API task definition
           \--> worker task definition
           \--> one-off Alembic migration task

GitHub Actions --OIDC--> scoped AWS deploy role
ECS stdout/stderr ------> CloudWatch Logs and alarms
```

The API and worker always deploy the same image digest. Different commands make
them separate runtime roles.

ECS tasks use private application subnets with `assign_public_ip = false`.
For the portfolio deployment, each application-subnet route table sends
`0.0.0.0/0` to one NAT Gateway in a public subnet. This gives the API, worker,
and migration task the outbound HTTPS path required for ECR image pulls,
CloudWatch Logs, Secrets Manager, and external LLM/provider APIs. An S3 gateway
endpoint keeps S3 traffic off the NAT path. RDS and ElastiCache use isolated
data-subnet route tables with no default internet route.

One NAT Gateway is an intentional, cost-conscious, non-high-availability
starting point: its Availability Zone is a documented failure domain. Before
claiming a highly available environment or running more than a short portfolio
demo, provision one NAT Gateway per application-subnet Availability Zone and
route each subnet to its local NAT. Interface endpoints for ECR, CloudWatch
Logs, and Secrets Manager are a later measured optimization when their fixed
cost is justified by NAT traffic and availability requirements.

## Phase 0: account and safety foundation

Complete before persistent resources are provisioned:

- [ ] Choose the AWS account and initial region.
- [ ] Enable MFA for human administrator access.
- [ ] Define least-privilege human, CI, execution, and task roles.
- [ ] Configure project/environment/owner/cost tags.
- [ ] Create the USD 120 monthly AWS Budget with the actual and forecast alerts
      and response policy defined below.
- [ ] Decide staging and production-demo DNS names.
- [ ] Record the synthetic-data-only boundary.
- [ ] Confirm that no real-data/KVKK claim is implied by the deployment.

## Phase 1: Terraform foundation

- [ ] Add remote-state design without committing state or secrets.
- [ ] Create separate staging and production-demo state.
- [ ] Provision VPC, public ALB/NAT subnets, private application subnets, and
      isolated data subnets across at least two Availability Zones.
- [ ] Set `assign_public_ip = false` on ECS tasks and route each private
      application subnet through the initial single NAT Gateway.
- [ ] Add an S3 gateway endpoint to the application route tables; keep RDS and
      ElastiCache data route tables without a default internet route.
- [ ] Record the single-NAT failure domain and the per-AZ NAT upgrade gate in
      Terraform outputs and the operations runbook.
- [ ] Provision ECR and image-retention rules.
- [ ] Provision ECS cluster and CloudWatch log groups.
- [ ] Provision private RDS PostgreSQL and verify the required pgvector version.
- [ ] Enable RDS automated backups and point-in-time recovery.
- [ ] Provision private TLS-enabled ElastiCache.
- [ ] Provision a private, encrypted, versioned S3 bucket with lifecycle rules.
- [ ] Create Secrets Manager entries/references.
- [ ] Configure GitHub OIDC trust restricted to this repository and approved
      branches/environments.

## Phase 2: application production boundaries

Complete before the AWS alpha:

- [ ] Add `/health/ready` for database and required Redis reachability.
- [ ] Add an S3 storage adapter behind the document storage interface.
- [ ] Replace process-local browser sessions with an ElastiCache-backed store,
      including TTL, rotation, invalidation, and replay tests.
- [ ] Configure PostgreSQL and ElastiCache TLS URLs.
- [ ] Confirm structured logs contain correlation IDs but no secrets, tokens,
      raw document bodies, or unnecessary PII.
- [ ] Define API and worker task commands from the same image.
- [ ] Define a one-off migration task using the same release image.
- [ ] Add graceful worker shutdown and task visibility/retry evidence.

## Phase 3: GitHub Actions deployment

The deployment workflow must:

1. Run all quality, test, migration, and container checks.
2. Assume the AWS deploy role through GitHub OIDC.
3. Build once and push a commit-SHA image to ECR.
4. Resolve the immutable image digest.
5. Record backup/migration risk.
6. Run the migration ECS task and require success.
7. Register API and worker task definitions using the same digest.
8. Deploy staging and wait for service stability.
9. Require ALB readiness and run API/worker smoke tests.
10. Record the deployed commit, task-definition revisions, migration revision,
    and smoke-test result.

Production-demo deployment requires a protected GitHub environment and explicit
approval.

## Phase 4: rollback and restore rehearsal

Application rollback:

- Restore the previous API and worker task-definition revisions.
- Confirm the previous image is compatible with the current schema.
- Wait for ECS stability and rerun smoke tests.

Database recovery:

- Prefer backward-compatible expand/contract migrations.
- Do not automate destructive `alembic downgrade` in production.
- For data loss/corruption, restore RDS to a new instance using point-in-time
  recovery and follow a reviewed cutover procedure.

Object recovery:

- Restore an earlier S3 object version.
- Verify tenant authorization still governs the restored object.

## Phase 5: observability and security evidence

- [ ] CloudWatch log groups have finite retention.
- [ ] ALB target-health, 5xx, and latency alarms exist.
- [ ] ECS API/worker restart alarms exist.
- [ ] Worker heartbeat and queue-backlog signals exist.
- [ ] RDS and ElastiCache health alarms exist.
- [ ] Logs pass a PII/secret scan.
- [ ] RDS and ElastiCache have no public route.
- [ ] S3 Block Public Access is enabled.
- [ ] Task roles cannot access unrelated buckets/secrets.
- [ ] CloudTrail captures deployment/control-plane activity.
- [ ] Cost dashboard and budget alerts are reviewed.

## Week mapping

| Milestone | AWS outcome |
| --- | --- |
| Week 4 | S3-ready storage adapter, reliable worker/task boundaries |
| Week 5 alpha | Staging infrastructure, ECR/ECS deployment, migration task, live smoke, rollback rehearsal |
| Week 9 beta | AI/RAG release through the same digest-based pipeline |
| Week 11 RC | Restore drill, security/load checks, 48-hour observation |
| Week 12 v1.0 | Protected production-demo release, runbook, cost/evaluation evidence, demo |

## Ownership

| Area | Lead | Reviewer/pair |
| --- | --- | --- |
| Week 5 alpha release and migration | Emir | Eray |
| CI image/OIDC workflow | Eray | Emir |
| Network/IAM threat review | Shared | Shared |
| RDS/pgvector and backup proof | Emir | Eray |
| ECS worker reliability | Eray | Emir |
| ElastiCache sessions and queue boundary | Shared | Shared |
| Week 11 restore/rollback rehearsal | Eray | Emir |
| Week 12 production-demo release | Shared | Shared |

## Explicit cost boundary

AWS resources are not free merely because traffic is low. Persistent ALB, NAT,
RDS, and ElastiCache resources can dominate idle cost. The team must:

- create a USD 120 monthly cost budget before staging;
- notify both Emir and Eray through verified email/SNS subscriptions at 50%,
  80%, and 100% of actual spend, and at 80% and 100% of forecast spend;
- treat 50% as informational; at 80% actual or forecast, freeze new paid
  resources, review Cost Explorer and mandatory tags within 24 hours, and
  destroy idle staging resources; at 100% actual or forecast, stop nonessential
  deployments and destroy disposable staging within 24 hours unless both owners
  document a time-bounded exception;
- remember that AWS Budgets data can be delayed and alerts are not a hard
  spending cap; the owners remain responsible for checking Cost Explorer;
- use the smallest measured configuration that meets the demo baseline;
- avoid premature multi-AZ or autoscaling claims;
- never keep staging and production-demo persistent simultaneously without a
  documented milestone exception, and destroy an idle disposable environment
  within 48 hours;
- retain CloudWatch logs for 14 days in staging and 30 days in production-demo,
  keep only the current and immediately previous ECR release images, expire
  noncurrent S3 object versions after 30 days, and delete temporary database
  restore-test snapshots within 7 days.
