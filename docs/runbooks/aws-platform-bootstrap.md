# AWS platform bootstrap runbook

This runbook creates only the retained Terraform state foundation, the monthly
budget, and GitHub OIDC/IAM roles for Issue #37. It does not create VPC, ECS,
RDS, ElastiCache, ALB, or document-storage workloads, and it is not evidence
that SupportFlow has been deployed on AWS.

## Preconditions

- Eray is the sole Terraform Apply/Release Captain.
- The AWS root user is protected by MFA and is not used for routine commands.
- Human access uses an individual MFA-backed or IAM Identity Center session;
  no long-lived access key is saved in the repository or GitHub.
- Billing access works, Free Tier/credit expiry is recorded privately, and the
  real notification addresses for both owners are available.
- Terraform `>= 1.10` and AWS CLI v2 are installed.
- The pull request head and a secret-free plan are reviewed before either
  `terraform apply` command below.

## 1. Verify the operator identity

Authenticate with the approved short-lived profile, then verify the account
before creating a plan:

```powershell
$env:AWS_PROFILE = "supportflow-admin"
$env:AWS_REGION = "eu-central-1"
aws sts get-caller-identity
```

Stop if the account or region is not the approved #37 target. Do not paste raw
credential or state output into GitHub.

## 2. Plan and apply the retained state foundation

```powershell
Copy-Item infra/aws/bootstrap/terraform.tfvars.example infra/aws/bootstrap/terraform.tfvars
terraform -chdir=infra/aws/bootstrap init
terraform -chdir=infra/aws/bootstrap fmt -check
terraform -chdir=infra/aws/bootstrap validate
terraform -chdir=infra/aws/bootstrap test
terraform -chdir=infra/aws/bootstrap plan -out=bootstrap.tfplan
terraform -chdir=infra/aws/bootstrap show -no-color bootstrap.tfplan
```

Review the displayed plan for the expected KMS key and one private, versioned,
publicly blocked state bucket. The saved plan is ignored and must not be
committed. After approval, apply that exact plan:

```powershell
terraform -chdir=infra/aws/bootstrap apply bootstrap.tfplan
terraform -chdir=infra/aws/bootstrap output
```

## 3. Migrate bootstrap state into the retained bucket

Use the non-secret bucket and KMS outputs to fill local copies. These generated
files are ignored:

```powershell
Copy-Item infra/aws/bootstrap/backend.hcl.example infra/aws/bootstrap/backend.hcl
Copy-Item infra/aws/bootstrap/backend_override.tf.example infra/aws/bootstrap/backend_override.tf
terraform -chdir=infra/aws/bootstrap init -migrate-state -backend-config=backend.hcl
terraform -chdir=infra/aws/bootstrap state list
```

Confirm that the remote state object and `.tflock` behavior use the expected
bucket/KMS key. Retain no local `.tfstate` or backup after remote state is
verified; handle any local backup as sensitive material outside the repository.
The state bucket and KMS key have `prevent_destroy` and remain after evidence
workloads are removed.

## 4. Plan and apply budget/OIDC/IAM foundation

Copy and fill the ignored files with the bootstrap outputs and real notification
emails:

```powershell
Copy-Item infra/aws/foundation/backend.hcl.example infra/aws/foundation/backend.hcl
Copy-Item infra/aws/foundation/terraform.tfvars.example infra/aws/foundation/terraform.tfvars
terraform -chdir=infra/aws/foundation init -backend-config=backend.hcl
terraform -chdir=infra/aws/foundation fmt -check
terraform -chdir=infra/aws/foundation validate
terraform -chdir=infra/aws/foundation test
terraform -chdir=infra/aws/foundation plan -out=foundation.tfplan
terraform -chdir=infra/aws/foundation show -no-color foundation.tfplan
```

If the account already contains the GitHub OIDC provider, import its exact ARN
instead of trying to create a duplicate, record that fact in the PR, and rerun
the plan:

```powershell
terraform -chdir=infra/aws/foundation import aws_iam_openid_connect_provider.github "arn:aws:iam::<account-id>:oidc-provider/token.actions.githubusercontent.com"
```

The reviewed plan must show eight budget notifications: actual and forecast at
USD 10, 25, 50, and 120. It must show three distinct OIDC roles and an exact
`repo:kanfan/supportflow-ai:environment:staging` subject without wildcards.
The Terraform apply role intentionally has read-only AWS discovery plus exact
state/lock access in this slice; the workload PR must add reviewed, non-IAM
mutation permissions before it can be used for apply.
After approval:

```powershell
terraform -chdir=infra/aws/foundation apply foundation.tfplan
terraform -chdir=infra/aws/foundation output
```

Each email subscriber must confirm the AWS Budgets subscription. Alerts can be
delayed and are not a hard cap; the 24-hour workload teardown rule remains the
primary cost control.

## 5. Configure the protected GitHub environment

Create or update the `staging` GitHub environment with:

- deployment branch restricted to `main`;
- Emir as required reviewer;
- self-review disabled;
- `SUPPORTFLOW_ALLOWED_DEPLOY_REF=refs/heads/main`;
- `SUPPORTFLOW_OIDC_AUDIENCE=sts.amazonaws.com`;
- `SUPPORTFLOW_OIDC_SUBJECT=repo:kanfan/supportflow-ai:environment:staging`;
- `SUPPORTFLOW_ENVIRONMENT_REVIEW_REQUIRED=true`;
- `SUPPORTFLOW_ENVIRONMENT_NO_SELF_REVIEW=true`.

Do not use the Terraform apply role or set `SUPPORTFLOW_AWS_ROLE_ARN` for a live
release until the next workload slice supplies reviewed mutation permissions
and exact ECS execution/task role ARNs, and Terraform reports both live-ready
outputs as `true`.

## Retained evidence

Record only the exact commit, Terraform/provider versions, sanitized plan
summary, account ID, region, state bucket/KMS ARNs, budget name/thresholds,
role ARNs, OIDC subject, subscription-confirmation result, and known manual
prerequisites. Never retain state content, plan files, email addresses, account
credentials, or secret values in GitHub.
