# ADR 0007: Ephemeral AWS portfolio-evidence environment

- Status: Accepted
- Date: 2026-08-13
- Owners: Emir and Eray
- Tracks: #37, #38, #39, and #40
- Refines: ADR 0004 and ADR 0006

## Context

SupportFlow is a learning and resume project. The team wants credible evidence
that the application can be deployed through production-shaped AWS practices,
but it is not operating a startup, a customer service, or a continuously
available production system.

Keeping ALB, NAT Gateway, RDS, ElastiCache, ECS, CloudWatch, and the ClamAV
worker sidecar running between demonstrations would create cost and operational
burden without improving the portfolio claim. A successful deployment alone is
also insufficient: the useful evidence is the repeatable path from reviewed
Terraform and OIDC through migration, verification, recovery, and teardown.

## Decision

### Purpose and claim boundary

AWS is used to produce a real, production-shaped deployment evidence package.
The team may claim that SupportFlow was deployed and verified on AWS only when
the corresponding evidence exists. It does not claim:

- commercial production readiness;
- continuous availability, an SLA, or a permanently hosted demo;
- real-customer operation or regulatory compliance; or
- high availability from the deliberately cost-conscious one-NAT topology.

The AWS environment contains synthetic data only. Real customer, interview, or
pilot data requires a separate privacy, retention, and operational decision and
is outside this environment.

### Ephemeral lifecycle

There is one short-lived **portfolio evidence environment**. Each approved
verification or demo window follows the same lifecycle:

1. review a secret-free Terraform plan and whole-environment cost estimate;
2. apply through the protected GitHub OIDC workflow;
3. run the one-off migration with the immutable application image digest;
4. deploy API, worker, and scanner and seed only deterministic synthetic data;
5. run smoke, security, observability, rollback, restore, and cost checks;
6. capture sanitized evidence tied to the commit, image digest, migration, and
   Terraform state revision;
7. run and verify `terraform destroy` within 24 hours of completing the
   evidence window.

The environment is recreated for a later milestone instead of remaining idle.
Terraform state and the minimum bootstrap resources needed for safe recreation
may remain, subject to the reviewed state-backend and cost policy. Workloads,
databases, caches, load balancers, NAT Gateway, buckets created for synthetic
evidence, and other paid disposable resources do not remain running merely to
preserve a public demo URL.

### Cost controls

Before the first apply, verified notifications reach both owners at USD 10,
USD 25, and USD 50 of actual and forecast monthly cost. USD 120 remains an
emergency ceiling, not an operating budget.

- At USD 10, verify tags, Cost Explorer allocation, and the current estimate.
- At USD 25, freeze any new paid resource or scope increase and review the
  active evidence checklist.
- At USD 50, stop nonessential work and destroy disposable resources unless
  both owners record a short, costed exception with an explicit expiry.
- At USD 120, treat the event as a control failure, stop deployments, destroy
  all disposable project resources, and perform a written cost review.

AWS Budgets can be delayed and is not a hard real-time spending cap. The 24-hour
destroy deadline therefore remains mandatory even when no alert has fired.

### Evidence retained after destroy

The repository may retain only sanitized artifacts needed to reproduce and
explain the work:

- reviewed Terraform plan summary and non-secret outputs;
- GitHub OIDC, immutable-digest, migration, and deployment evidence;
- API, worker, scanner, tenant-isolation, session, and readiness results;
- rollback, RDS restore, and S3 version-recovery evidence;
- redacted CloudWatch metrics/log samples, alarms, and sensitive-data scan;
- budget notifications, cost snapshot, resource inventory, and successful
  teardown evidence; and
- runbooks that recreate and destroy the environment.

Credentials, secret values, Terraform state, raw logs containing sensitive
values, and synthetic document bodies are not portfolio artifacts.

### Scanner decision

The ClamAV sidecar remains part of the worker task. Its memory cost is acceptable
because the task exists only during bounded verification windows. The scanner
still requires a digest-pinned image, fail-closed adapter behavior, deterministic
signature-freshness health, bounded worker concurrency, and sufficient Celery
timeout/visibility budget. Ephemeral operation reduces cost; it does not weaken
the runtime safety contract.

### Product validation boundary

Product/usability validation is separate from AWS technical acceptance. It may
use the local stack or a newly created synthetic AWS demo window. The AWS
environment is not kept alive while waiting for interviews or feedback, and no
real-user validation data is loaded into it without a new recorded decision.

## Consequences

### Positive

- The portfolio demonstrates real Terraform, OIDC, ECS, managed dependencies,
  scanner, observability, recovery, and teardown work.
- Cost is bounded by lifecycle design rather than by hope or an alert alone.
- Recreating the environment proves infrastructure reproducibility.
- The public claim remains honest and easy to explain in an interview.

### Trade-offs

- There is no permanent public demo URL.
- Every later AWS milestone needs a planned recreation window.
- Evidence capture and teardown verification become release requirements.
- Some production characteristics are demonstrated structurally rather than by
  long-duration traffic or availability observation.

## Superseded wording

Where ADR 0004, ADR 0006, the deployment plan, issues, or roadmap mention
`staging`, `production-demo`, `persistent apply`, 48-hour idle teardown, or a
USD 120 monthly operating budget, interpret them through this ADR:

- `staging` means the short-lived portfolio evidence environment;
- there is no separately persistent `production-demo` environment;
- an apply creates temporary paid resources for an approved evidence window;
- disposable resources are destroyed within 24 hours after evidence capture;
- USD 10/25/50 are early warning and response thresholds; and
- USD 120 is an emergency ceiling only.
