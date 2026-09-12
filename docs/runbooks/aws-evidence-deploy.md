# AWS evidence deployment runbook

Use this runbook only after Issue #37's workload Terraform plan is approved and
applied, all required non-secret GitHub environment variables are populated,
`deployment_role_live_ready=true`, and synthetic smoke secrets are configured.

## Pre-deploy gates

1. Confirm the exact `main` commit and both repository CI jobs are green.
2. Confirm the whole-window estimate, USD 10/25/50/120 notifications, and the
   24-hour teardown deadline.
3. Confirm Terraform state locking is healthy and no plan/apply/destroy is in
   progress.
4. Capture a fresh RDS backup/check reference and schema-compatibility record.
5. Confirm ECS desired counts are zero before the first bootstrap release and
   that only deterministic synthetic data will be loaded.
6. Confirm the `staging` environment requires Emir's approval and prevents
   self-review.

## First release into a fresh workload shell

Run the protected workflow from `main`:

```powershell
gh workflow run aws-deploy.yml --repo kanfan/supportflow-ai --ref main `
  -f operation=deploy `
  -f deployment_mode=bootstrap `
  -f environment=staging `
  -f backup_reference="<fresh-reference>" `
  -f schema_compatibility_reference="<reviewed-reference>"
```

The workflow must use one immutable ECR digest for migration, API, and worker;
run migration before service promotion; attach the release task definitions and
desired count `1/1` atomically; pass ALB readiness and synthetic application
smoke; scan release logs; and upload only sanitized evidence.

## Later upgrade

Use `deployment_mode=upgrade`. The workflow captures the currently serving
API/worker revisions and digest before migration and preserves existing desired
counts. Store the prior compatible revision/digest values for rollback; do not
copy raw CloudWatch logs or smoke secrets into the repository.

## Stop conditions

Do not promote or claim AWS deployment if migration, digest parity, readiness,
smoke, log scan, budget review, or evidence upload fails. Use a forward fix when
safe; use the reviewed rollback procedure only with compatible schema evidence.
Never run `alembic downgrade` as application rollback.
