# SupportFlow AWS infrastructure

This directory contains the Terraform foundation for Issue #37. It is split to
handle AWS's one-time trust and remote-state bootstrap without committing local
state or long-lived credentials.

- `bootstrap/` creates the retained KMS-encrypted, versioned S3 state bucket.
  It begins with local state and is migrated into that bucket immediately after
  its first reviewed apply.
- `foundation/` uses the remote S3 backend and creates the account-wide budget,
  GitHub OIDC provider, and separate Terraform plan/apply and application
  deployment roles.

No workload resources are created in this slice. VPC, ECS, RDS, ElastiCache,
S3 document storage, Secrets Manager, alarms, and application task roles belong
to the next reviewed platform slice. The Terraform apply role remains
read-only and the deployment role cannot pass an ECS role until that slice
supplies explicit, reviewed permissions and role ARNs.

Start with the [platform bootstrap runbook](../../docs/runbooks/aws-platform-bootstrap.md).
Never commit `.tfstate`, saved plan files, generated backend configuration,
credentials, notification email addresses, or secret values.
