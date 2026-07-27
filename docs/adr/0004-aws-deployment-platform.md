# ADR 0004: AWS deployment platform

- Status: Accepted
- Date: 2026-07-27
- Owners: Emir and Eray

## Context

The original project plan selected Render for the portfolio environment.
The team has replaced that choice with AWS so both contributors can practice
container deployment, private networking, managed data services, IAM, release
automation, observability, backup, rollback, and cost control.

The application remains a modular monolith. FastAPI and Celery share one
repository and one image, while API and worker processes run independently.
The decision must preserve that simplicity and must not turn the learning
project into an EKS or microservices migration.

The portfolio release uses synthetic or explicitly approved data. Choosing AWS
does not by itself satisfy KVKK, data-transfer, retention, or production
readiness requirements.

## Decision

### Region and environments

- `eu-central-1` is the initial technical default because the target users are
  in Türkiye and the project needs a nearby EU region.
- The final region remains subject to the documented KVKK/data-transfer gate.
- Local development uses Docker Compose.
- Staging and production-demo use separate Terraform state, resource names,
  secrets, databases, buckets, and ECS services.
- Production-demo remains a portfolio environment with synthetic or explicitly
  approved data. It is not presented as a commercial production system.

### Runtime and ingress

- One private Amazon ECR repository stores immutable images tagged with the Git
  commit SHA. Releases deploy by image digest.
- The FastAPI API runs as an Amazon ECS service on AWS Fargate.
- The Celery worker runs as a separate ECS/Fargate service using the same image
  digest and a worker-specific command.
- An internet-facing Application Load Balancer terminates HTTPS with an ACM
  certificate and sends traffic only to the API target group.
- The ALB health check uses a readiness endpoint. The current liveness endpoint
  is insufficient for deployment gating because it does not prove database or
  Redis reachability.
- The worker is not exposed through a load balancer.

### Data and state

- Amazon RDS for PostgreSQL stores relational data and pgvector embeddings.
  The chosen PostgreSQL engine version must support the required `pgvector`
  extension before infrastructure is applied.
- RDS automated backups and point-in-time recovery are enabled. A manual
  snapshot is taken before a migration with meaningful rollback risk.
- Amazon ElastiCache for Valkey or Redis OSS provides the Celery broker/result
  backend and the shared browser-session store. Connections require TLS and
  authentication. Key prefixes separate queue, result, cache, rate-limit, and
  session concerns.
- Browser sessions must use server-side TTLs and invalidation in ElastiCache
  before the API scales beyond one task or claims restart-resilient sessions.
- Amazon S3 stores uploaded source documents in a private bucket. Public access
  is blocked, default encryption remains enabled, versioning is enabled, and
  lifecycle/retention rules are explicit.
- S3 object keys are tenant-namespaced, but authorization always comes from
  verified database context and IAM/task-role policy, never from an object-key
  prefix alone.

### Network and IAM

- The ALB occupies public subnets.
- ECS tasks, RDS, and ElastiCache occupy private subnets.
- The database accepts traffic only from the ECS task security group.
- ElastiCache accepts traffic only from the ECS task security group.
- ECS uses separate execution and application task roles.
- The execution role may pull ECR images, write configured logs, and resolve
  named secrets.
- The application task role receives only the required S3 and service actions.
  It does not receive broad administrator permissions.
- RDS and ElastiCache are never public.

### Secrets and configuration

- AWS Secrets Manager stores database credentials, the authentication/session
  secret, Redis authentication material, and external provider secrets.
- Non-secret configuration remains explicit in task definitions or a reviewed
  configuration mechanism.
- Secrets are never committed to GitHub, placed in Docker images, printed by
  CI, or copied into CloudWatch logs.
- A rotated secret reaches running containers only after a new ECS deployment;
  the runbook must include that step.

### CI/CD and migrations

GitHub Actions uses OIDC federation to assume narrowly scoped AWS roles. It does
not store long-lived AWS access keys.

The deployment sequence is:

1. Run lint, format, type checking, unit/integration tests, migration checks,
   and container smoke.
2. Build one image and push the commit-SHA tag to ECR.
3. Resolve and record the immutable ECR image digest.
4. Evaluate backup and migration compatibility.
5. Run `alembic upgrade head` as a one-off ECS task using that image.
6. Deploy the same digest to the API and worker task definitions.
7. Wait for ECS service stability and ALB readiness.
8. Run authentication, ticket, document, queue, and worker smoke tests.
9. Promote the verified revision or roll the services back to the previous task
   definition.

Application rollback and database rollback are separate decisions. Production
migrations use backward-compatible expand/contract changes. A failed
application release may return to the previous image only while the migrated
schema remains compatible. Destructive automatic Alembic downgrades are not a
normal production rollback mechanism.

### Observability and cost controls

- ECS containers send structured standard output/error to CloudWatch Logs using
  the `awslogs` driver.
- Log retention is finite and environment-specific.
- Logs must pass the existing PII/secret rules.
- CloudWatch alarms cover ALB target health, API errors/latency, ECS task
  restarts, worker heartbeat/queue backlog, RDS health, and ElastiCache health.
- CloudTrail records AWS control-plane activity.
- Every resource is tagged with project, environment, owner, and cost-center
  values.
- AWS Budgets is configured before the first persistent staging deployment,
  with actual-cost alerts at agreed thresholds.

### Infrastructure as code

Terraform is the selected default for AWS infrastructure. Staging and
production-demo use separate state and variable sets. State and plan artifacts
must not expose secrets and must not be committed to the repository.

The first infrastructure modules cover:

- VPC, subnets, routing, and security groups;
- ECR;
- ECS cluster, API service, worker service, task definitions, and migration
  task;
- ALB, target group, HTTPS listener, ACM integration, and DNS input;
- RDS PostgreSQL;
- ElastiCache;
- S3;
- Secrets Manager references;
- CloudWatch log groups, dashboards, and alarms;
- GitHub OIDC provider/roles or their pre-provisioned equivalents;
- budgets and mandatory tags.

## Timeline changes

- Week 4 remains document ingestion and reliable background jobs. The storage
  adapter targets S3, while local tests use a fake or local adapter.
- Week 5 becomes the AWS alpha milestone. Emir is Release Captain; Eray is CI
  Captain. The team provisions staging, deploys API and worker, runs the
  migration task, seeds synthetic demo data, verifies smoke tests, and rehearses
  rollback.
- Week 9 deploys the AI/RAG beta through the same ECR/ECS path.
- Week 11 performs restore, rollback, load, security, and 48-hour release
  candidate observation on AWS.
- Week 12 publishes v1.0 through the AWS pipeline and completes the runbook,
  release evidence, cost report, demo, and portfolio material.

## Consequences

### Positive

- Both contributors gain direct AWS, IAM, network, container, managed database,
  observability, and release experience.
- API and worker retain one image and one codebase.
- Managed PostgreSQL, Redis-compatible storage, object storage, and monitoring
  match the existing application boundaries.
- OIDC avoids long-lived deployment credentials.

### Costs and trade-offs

- AWS is more operationally complex than Render.
- ALB, NAT/networking, RDS, and ElastiCache can create meaningful idle cost.
- The team must manage IAM, subnets, security groups, backups, log retention,
  and budgets explicitly.
- Process-local UI sessions cannot survive ECS replacement or horizontal
  scaling and therefore require the ElastiCache adapter.
- Production-like deployment claims require restore and rollback evidence, not
  only a successful first deployment.

## Non-goals

- Amazon EKS or Kubernetes
- Splitting the modular monolith into microservices
- Multi-region active-active deployment
- Commercial production readiness or a KVKK compliance claim
- Automatic database downgrade during rollback
- Autoscaling before a measured baseline exists
- Public RDS, public ElastiCache, or public S3 objects

## Required evidence before the AWS alpha is accepted

- Terraform plan is reviewed and contains no secret values.
- GitHub Actions assumes an AWS role through OIDC.
- One image digest runs in both API and worker task definitions.
- RDS migration reaches `head` through a one-off task.
- ALB readiness, API health, and Celery smoke pass.
- RDS and ElastiCache are private.
- S3 public access is blocked and upload authorization is tenant-scoped.
- Secrets do not appear in GitHub Actions or CloudWatch logs.
- Browser sessions use ElastiCache before scaling beyond one API task.
- Previous application task definition can be restored safely.
- Backup/restore steps and AWS cost alerts are demonstrated.

## References

- [Amazon ECS service load balancing](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/service-load-balancing.html)
- [Amazon RDS for PostgreSQL extension versions](https://docs.aws.amazon.com/AmazonRDS/latest/PostgreSQLReleaseNotes/postgresql-extensions.html)
- [Amazon ElastiCache in-transit encryption](https://docs.aws.amazon.com/AmazonElastiCache/latest/dg/in-transit-encryption.html)
- [Inject Secrets Manager secrets into ECS](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/secrets-envvar-secrets-manager.html)
- [Send ECS logs to CloudWatch](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/using_awslogs.html)
- [OIDC federation in AWS IAM](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_providers_oidc.html)
- [Amazon S3 security best practices](https://docs.aws.amazon.com/AmazonS3/latest/userguide/security-best-practices.html)
- [RDS backup and point-in-time recovery](https://docs.aws.amazon.com/AmazonRDS/latest/gettingstartedguide/managing-backup-restore.html)
- [AWS Budgets](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-managing-costs.html)
