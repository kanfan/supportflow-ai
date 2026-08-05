# ADR 0006: Week 5 AWS delivery ownership and handoff

- Status: Accepted
- Date: 2026-08-04
- Owners: Emir and Eray
- Tracks: #37, #38, #39, and #40
- Prerequisite: Week 4 merged into `main` as `4913064`

## Context

ADR 0004 selected the AWS topology and originally named Emir as Week 5 Release
Captain and Eray as CI Captain. The team has since refined the collaboration
model.

Eray will own the AWS account and platform bootstrap, as he owned the initial
Docker/PostgreSQL/Redis infrastructure foundation. Emir will develop and verify
the application against documented platform contracts without creating or
administering an AWS account.

This is an ownership change, not an architectural change. ADR 0004 remains the
authority for region, topology, private networking, managed services, OIDC,
budgets, backups, rollback, and acceptance evidence.

The handoff must avoid two failure modes:

- account or console state that only one contributor understands and cannot be
  reproduced from the repository;
- sharing root credentials or long-lived AWS keys to make application work
  possible.

## Responsibility summary

This table is the canonical Week 5 ownership boundary:

| Work | Eray | Emir |
| --- | --- | --- |
| AWS account, root security, MFA, billing, and budgets | Owns and operates | No setup or administration responsibility |
| Terraform state, network, IAM, RDS, ElastiCache, S3, ECS, and applies | Implements and operates | Reviews code, plans, boundaries, cost, and sanitized evidence |
| GitHub OIDC and immutable deployment pipeline | Implements and operates releases | Reviews gates, digest parity, migration, and rollback evidence |
| Non-secret platform outputs and runbooks | Produces the handoff | Reviews and consumes the handoff |
| S3/scanner/session/readiness application code | Provides platform contracts and reviews | Implements and tests |
| Week 5 staging technical acceptance | Performs required AWS operations and supplies platform evidence | Leads the verification matrix and evidence document |
| Two-user product validation | Supports as a contributor | Starts only after #40 technical acceptance |

Unambiguous rules:

- Emir does not create, secure, fund, or administer the AWS account and does not
  perform Week 5 Terraform applies.
- Eray does not make application code depend on undocumented console state. He
  hands off Terraform, non-secret outputs, scoped OIDC workflows, and runbooks.
- Reviewing infrastructure does not make Emir its operator. Operating the
  platform does not make Eray the permanent sole owner of merged infrastructure
  code.
- Emir may develop #38 locally after this contract is accepted, but live AWS
  integration and #40 remain blocked until #37 supplies the required handoff.

## Decision

### Roles

#### Eray: AWS Platform and Release Captain

Eray owns:

- AWS account/root security, MFA, billing, and administrative access;
- budget subscriptions, cost response, tags, and teardown controls;
- Terraform backend/state bootstrap and infrastructure applies;
- VPC, IAM, ECR, ECS, ALB, RDS, ElastiCache, S3, Secrets Manager, CloudWatch,
  CloudTrail, DNS, and TLS foundations;
- GitHub OIDC platform roles and the immutable-image deployment pipeline;
- initial staging creation, platform operations, and infrastructure teardown;
- platform evidence and non-secret Terraform outputs required by application
  work.

Eray is the single Terraform Apply/Release Captain for Week 5. This prevents two
branches or human sessions from racing the same staging state.

#### Emir: Application Integration and Verification Lead

Emir owns:

- review of Terraform plans, security boundaries, cost assumptions, outputs,
  and operational runbooks through GitHub;
- the S3 `DocumentStorage` adapter and its application-level tests;
- real scanner integration behind the accepted scanner port;
- ElastiCache-backed browser sessions and Redis/TLS application configuration;
- dependency readiness, safe structured logging, and correlation identifiers;
- review that migration, API, and worker use one immutable release digest;
- the synthetic staging technical-acceptance matrix and Week 5 evidence.

Emir does not need AWS root, billing, administrator, or long-lived IAM
credentials. Application development remains reproducible locally through
ports, fakes, contract tests, Docker Compose, and GitHub Actions. Live AWS
operations are performed by Eray or the scoped OIDC workflow.

If interactive AWS inspection later becomes necessary, it uses a separate
time-bounded least-privilege read-only or task-specific role. It never uses root
or shared credentials.

### Work packages and dependencies

Week 5 uses four issues:

1. #37: Eray provisions the AWS platform and publishes the handoff contract.
2. #38: Emir implements application-facing AWS adapters and readiness.
3. #39: Eray implements the immutable OIDC deployment pipeline.
4. #40: Emir leads integrated staging technical acceptance with Eray providing
   platform and deployment evidence.

The dependency order is:

```text
ADR 0006 accepted
  |-> #37 account safety + budget + state + platform/plan work
  |-> #38 local adapters + contract tests
  \-> #39 deployment pipeline scaffolding
  -> #37 safety/state/plan/teardown gates reviewed
  -> Eray performs persistent staging apply
  -> sanitized #37 outputs + contract-ready #38 + pipeline-ready #39
  -> live integration and staging release
  -> #40 technical acceptance
  -> separate two-user task-based product validation
```

The first three work streams begin in parallel after this ADR is accepted. #38
may implement local adapters and contract tests, and #39 may build pipeline
scaffolding, while #37 establishes the platform. Persistent AWS resources are
not applied before #37's account-safety, budget, state, reviewed-plan, and
teardown gates pass. Live #38/#39 integration waits for the relevant sanitized
#37 outputs.

### Platform handoff contract

The #37 handoff is repository-based and includes:

- reviewed Terraform code, version/provider lock, and plan without secrets;
- successful apply evidence;
- region, AWS account identifier, resource names/endpoints, security properties,
  and application configuration names as non-secret outputs;
- Secrets Manager key/ARN references without secret values;
- OIDC trust and GitHub environment contract;
- staging endpoint and ALB readiness contract;
- API, worker, migration, storage, scanner, database, Redis, and logging
  configuration contracts;
- deploy, rollback, RDS restore, S3 version recovery, cost response, and destroy
  runbooks;
- expected idle cost, one-NAT failure domain, manual console prerequisites, and
  remaining risks.

Console-only configuration is recorded as a temporary prerequisite and either
automated or documented precisely. The handoff is incomplete if application or
verification work depends on settings known only to one contributor.

### Credential and secret boundary

- Root credentials are never shared or used for routine work.
- No long-lived AWS access key is stored in GitHub or sent between contributors.
- GitHub Actions assumes scoped AWS roles through OIDC.
- Human access uses individual identities and least privilege if it is needed.
- Terraform state, plans, workflow output, CloudWatch logs, and repository
  evidence must not expose secret values.
- Emir consumes non-secret outputs and secret reference names, not secret
  contents.

### Review and apply boundary

The Week 5 infrastructure workflow is:

1. Eray opens a draft platform PR with Terraform and a sanitized plan.
2. Emir reviews network, IAM, state, cost, data, backup, output, and teardown
   boundaries.
3. Required changes are addressed on an exact head commit.
4. After approval, Eray performs the apply or triggers the protected OIDC
   workflow.
5. Eray records sanitized post-apply evidence and non-secret outputs.
6. Emir confirms that the #38 contract can be consumed without account
   administration.

Terraform apply is never an unreviewed pull-request side effect. Concurrent
deployment/apply workflows for the same environment are serialized.

### Application boundary

Application code does not hard-code account IDs, endpoints, bucket names,
credentials, or console-generated values. It consumes explicit non-secret
configuration and Secrets Manager references supplied by #37.

The S3 and scanner adapters preserve the Week 4 ports and safe error vocabulary.
Shared sessions preserve TTL, rotation, CSRF, logout invalidation, expiry, and
replay behavior. Readiness proves required PostgreSQL and Redis reachability
without turning lightweight liveness into a dependency check.

### Release and acceptance boundary

The immutable deployment pipeline builds once and records one digest. The
one-off migration task, API service, and worker service use that same digest.
Migration success, ECS stability, ALB readiness, and application/worker smoke
are required gates.

Week 5 is complete only after #40 records:

- authentication, ticket, audit, document, queue, and worker staging evidence;
- tenant/IDOR and sensitive-output negative cases;
- real scanner, private S3, shared sessions, TLS, and readiness evidence;
- previous-compatible-task-definition rollback;
- RDS restore and S3 object-version recovery procedure/evidence;
- private network, least-privilege IAM, OIDC, alarm, budget, and teardown
  evidence;
- exact commit, image digest, task definitions, migration revision, and CI runs.

Product validation is intentionally not performed before this technical gate.
After #40 passes, the team creates and runs the separate two-user task-based
usability validation required by the revised project timeline.

### Distributed handoff risk

Week 4 documents a process-crash window between the PostgreSQL upload commit and
Redis task publication. #37 must record an explicit Week 5 decision:

- implement an outbox/queued-record reconciler; or
- accept the risk for synthetic staging with a concrete detection and recovery
  procedure, while blocking real pilot data until it is resolved.

This decision does not block local application adapter work, but it is part of
the technical acceptance and real-pilot boundary.

## Consequences

### Positive

- Emir can practice application integration, architecture review, security
  reasoning, and release verification without administering an AWS account.
- Eray has one coherent platform and deployment ownership boundary.
- Terraform, outputs, workflows, and runbooks reduce dependency on one person's
  memory or console session.
- Individual identities and OIDC avoid credential sharing.
- Small dependent issues keep infrastructure, application, pipeline, and
  acceptance reviews understandable.

### Costs and trade-offs

- Emir depends on the #37 contract before live integration can finish.
- Eray is a temporary operational bottleneck and single apply owner.
- Repository evidence and runbooks require more discipline than an informal
  console handoff.
- Emir will learn AWS primarily through plans, code, contracts, evidence, and
  application behavior rather than initial account administration.

The single-owner apply rule is a Week 5 safety mechanism, not permanent code
ownership. After merge, infrastructure code remains shared and both contributors
must be able to explain it.

## Non-goals

- Sharing AWS root or administrator credentials
- Giving Emir responsibility for account creation or billing configuration
- Console-only infrastructure with no reproducible repository representation
- Production-demo or real-customer deployment in Week 5
- Changing the AWS topology selected by ADR 0004
- EKS, microservices, multi-region, or autoscaling work
- Product validation before the technical staging alpha is usable

## Approval

Eray reviewed exact head `05afb05` on 2026-08-05 and accepted the responsibility
matrix, credential boundary, issue ownership, and overall dependency model. His
single requested clarification was to show #37 platform work, #38 local adapter
work, and #39 pipeline scaffolding starting in parallel after ADR acceptance,
while preserving the #37 gates for persistent apply and live integration. This
revision records that clarification and changes the ADR to `Accepted`; the pull
request remains draft until the new exact head is re-reviewed.
