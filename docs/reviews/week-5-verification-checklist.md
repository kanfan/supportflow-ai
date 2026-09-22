# Week 5 AWS verification checklist

Status: preparation only; no live results recorded.
Tracks: #37 platform, #39 deployment, #40 technical acceptance.
Authority: ADR 0006 (ownership), ADR 0007 (ephemeral portfolio scope).

Emir leads verification and evidence review. Eray operates AWS, Terraform,
deployments, recovery, and teardown. All data is synthetic. Application work
completed in #38 provides the local foundation; it does not prove AWS behavior.

## Before scheduling the paid window

- [ ] PR #51 is merged and main Quality and Container smoke pass.
- [ ] Eray supplies #37 Terraform code, provider lock, secret-free plan summary,
      non-secret outputs, and explicit manual prerequisites for Emir's review.
- [ ] Account safety, state locking, scoped OIDC, protected environment approval,
      network/IAM boundaries, secret references, and CA delivery are reviewed.
- [ ] Whole-window cost estimate includes recovery targets and scanner capacity.
      USD 10/25/50 notification controls and the USD 120 emergency response are
      documented; notification delivery reaches both owners.
- [ ] Deploy, rollback, RDS restore, S3 version recovery, and destroy runbooks
      contain concrete commands for the reviewed platform.
- [ ] Deployment and teardown serialize on the same environment concurrency
      group: `supportflow-aws-<environment>`.
- [ ] Two synthetic tenants/users, documents, and secret/body canaries are ready.
      Credentials and fixture contents are excluded from retained evidence.
- [ ] Each check below has a runnable procedure from the handoff or acceptance
      implementation. Missing commands are resolved before apply.

Do not invent account identifiers, endpoints, Terraform paths, or dispatch
inputs. Attach commands from the accepted handoff when it exists. The current
checklist is preparation, not the final reproduction guide required by #40.

## Execution order and evidence

For every row record: result (`not run`, `pass`, `fail`, or `blocked`), UTC time,
operator, sanitized evidence link, and observed outcome. Start all rows as
`not run`. Distinguish repository tests, live observations, manual controls,
and accepted residual risks. A passing local test is not a live observation.

| Order | Check and expected outcome | Operator / reviewer | Evidence to retain |
| --- | --- | --- | --- |
| 1 | Reviewed Terraform apply creates the intended temporary resources | Eray / Emir | Commit, state revision reference, plan summary, apply run, non-secret outputs |
| 2 | Bootstrap migration succeeds before API/worker promotion; all three use one immutable image digest | Eray / Emir | CI/deploy run, digest, task revisions, Alembic revision, readiness/smoke result; bootstrap previous fields are `none` |
| 3 | Login, rotation, expiry, CSRF, logout replay and application replacement preserve the shared-session contract | Emir with Eray operating replacements | Scenario outcomes without cookies, tokens or credentials |
| 4 | Ticket list/detail/message/status and audit work; a second tenant cannot access or mutate the first tenant's records | Emir | Positive and negative scenario outcomes; no body or customer data |
| 5 | Document upload reaches ready; duplicate delivery is a no-op; corrupt/infected input fails safely; scanner unavailability and worker loss recover according to contract | Emir with Eray operating faults | State transitions, bounded retry/redelivery results, real scanner health/freshness evidence |
| 6 | ECS has no public IP; data services are private; TLS, private/versioned/encrypted S3, scoped roles and OIDC match the handoff | Eray / Emir | Sanitized configuration observations and access-denial results |
| 7 | Dependency outage changes readiness while liveness stays lightweight; recovery restores readiness | Eray operating faults / Emir | Before/outage/recovery observations |
| 8 | A subsequent upgrade captures a compatible previous release and preserves configured capacity | Eray / Emir | Upgrade run, previous/current revisions and digest, smoke result |
| 9 | Rollback restores the previous compatible API/worker release and passes smoke without database downgrade | Eray / Emir | Rollback run, schema compatibility reference, restored revisions/digest |
| 10 | RDS restore is demonstrated or rehearsed against a separate target; an earlier S3 object version is recovered with tenant authorization intact | Eray / Emir | Recovery runbook and observed/rehearsed result, clearly labelled; include temporary recovery resources in destroy inventory |
| 11 | The DB-commit-to-broker crash window has a recorded decision and concrete detection/recovery procedure | Eray / Emir | Exercised procedure or explicitly accepted synthetic-environment risk |
| 12 | Logs, API errors, audit metadata and Celery payload/results do not expose secrets or sensitive fixture contents | Emir with Eray supplying scoped observations | Scanner command/options and outcome; no raw logs or forbidden values |
| 13 | Required metrics, alarms, finite retention and cost controls exist | Eray / Emir | Sanitized control observations, notification-delivery proof, estimated/observed cost |
| 14 | Disposable resources are destroyed within 24 hours after evidence capture | Eray / Emir | Capture completion and destroy timestamps, destroy run, post-destroy inventory including recovery targets |

Bootstrap cannot supply rollback proof because it has no previous application
release. Use the subsequent upgrade to establish and verify the previous
compatible revision before the rollback exercise. Do not introduce an unrelated
feature merely to manufacture a deployment test.

Do not deliberately spend money to trigger budget thresholds. Record configured
thresholds and notification delivery separately from actual observed spend.
Budgets are delayed alerts, so the teardown deadline applies without an alert.

## Failure and cleanup

Stop promotion if migration fails. Record failed or blocked checks honestly;
capture enough sanitized evidence to reproduce the failure. Eray follows the
reviewed recovery/destroy runbook even if verification is incomplete. Never
leave the environment running while waiting for review or product feedback.

Inventory workload resources, recovery targets, snapshots/object versions and
other potentially billable leftovers. Identify any retained backend/bootstrap
resources explicitly, with their reviewed retention and cost policy. Record a
follow-up cost observation when billing data becomes available.

## Closing the milestone

- [ ] Add the final Week 5 evidence document with exact reproduction commands,
      release identity, run links, results, limitations and teardown proof.
- [ ] Both contributors review the evidence commit; unresolved acceptance
      failures remain visible and prevent claiming completion.
- [ ] #39 closes only after its live pipeline evidence is accepted. Coordinate
      #37/#40 closure using their full acceptance criteria, including teardown;
      platform availability alone is only the handoff needed to start testing.
- [ ] #40 closes after integrated acceptance and verified cleanup. Update the
      weekly learning document afterward.
- [ ] Separate two-user product validation starts after technical acceptance,
      using the local stack or a newly planned synthetic demo window.
