# AWS evidence-window teardown runbook

Disposable workload resources must be destroyed within 24 hours after #40
evidence capture. The retained Terraform state bucket/KMS key and the reviewed
budget/OIDC foundation are handled separately and are not part of routine
workload teardown.

## Preconditions

- The release/destroy concurrency key for `staging` is clear; no deploy,
  rollback, migration, restore, or evidence capture is running.
- Sanitized #40 evidence, required backup references, and restore-test results
  are retained.
- No real/customer data exists in the environment.
- The exact workload Terraform state and commit are recorded.

## Reviewed destroy

Use only the workload stack directory introduced by the next #37 slice:

```powershell
terraform -chdir=infra/aws/workload init -backend-config=backend.hcl
terraform -chdir=infra/aws/workload plan -destroy -out=destroy.tfplan
terraform -chdir=infra/aws/workload show -no-color destroy.tfplan
terraform -chdir=infra/aws/workload apply destroy.tfplan
```

Review the saved plan before apply. Do not use `-target`, `-auto-approve`, manual
console deletion, or state-file editing as the normal teardown path. Resolve S3
version-retention and RDS final-snapshot decisions through the reviewed workload
configuration, not an ad hoc force-destroy flag.

## Post-destroy verification

Verify that the selected workload backend/key has no remaining state entries,
then independently inventory the approved account and region. A normal plan
against the unchanged configuration would propose recreating the resources;
it is not a cleanup check.

```powershell
$remainingState = @(terraform -chdir=infra/aws/workload state list)
if ($LASTEXITCODE -ne 0) { throw "Could not read workload state" }
if ($remainingState.Count -ne 0) { throw "Workload state is not empty" }
aws resourcegroupstaggingapi get-resources --region eu-central-1 `
  --tag-filters Key=Project,Values=supportflow Key=Environment,Values=staging
aws ecs describe-clusters --clusters supportflow-staging --region eu-central-1
aws rds describe-db-instances --region eu-central-1
aws elasticache describe-replication-groups --region eu-central-1
```

For a destroyed ECS cluster, `describe-clusters` may return `MISSING` or a
transient `INACTIVE` cluster; `list-services` may return `ClusterNotFoundException`.
These are expected absence results only for the recorded cluster identifier.
Access denied, wrong account/region, and network failures are not absence proof.
An active cluster or surviving tasks/services require investigation.

The tag inventory must contain no unexplained disposable workload. Retained
bootstrap/foundation resources can appear and must match a recorded allowlist
of exact identifiers. Inventory temporary RDS restore instances, recovery
buckets/objects, and snapshots separately; an empty workload state does not
prove recovery targets were removed. Check ALB/NAT Gateway,
ECS tasks/services, RDS, ElastiCache, workload S3/ECR retention, CloudWatch log
retention, snapshots, and Secrets Manager recovery windows explicitly because
not every retained or recently deleted resource appears identically in the tag
API.

Record the destroy run, Terraform state revision, finish timestamp, retained
bootstrap/foundation resources, zero-workload inventory result, and any delayed
deletion cost. If inventory is not clean, #40 remains open and cost monitoring
continues until the exception is resolved.
