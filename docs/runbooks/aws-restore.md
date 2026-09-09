# AWS data recovery runbook

Recovery is rehearsed only with synthetic evidence data. Application rollback
and data restoration are different operations: restoring task definitions never
downgrades the database.

## RDS point-in-time restore rehearsal

1. Record the source database identifier, backup/PITR window, target timestamp,
   Terraform state revision, and incident/rehearsal reference.
2. Restore to a new temporary RDS instance in the isolated data subnets. Never
   overwrite the serving database or run an in-place destructive restore.
3. Apply the same private security group, parameter group with `rds.force_ssl=1`,
   encryption, and CA/hostname-verification contract.
4. Compose a temporary database URL in Secrets Manager; never place its password
   in Terraform output, shell history, GitHub variables, or evidence.
5. Run Alembic `check`, read-only integrity checks, tenant-isolation checks, and
   the relevant application smoke against the restore target.
6. Record only sanitized timestamps, identifiers, migration revision, check
   results, and duration. Remove the temporary instance through a reviewed
   Terraform change after evidence is accepted.

If the requested timestamp is outside the PITR window or schema compatibility
is unknown, stop and choose a forward repair or a separately reviewed recovery
plan.

## S3 version recovery rehearsal

1. Select a synthetic document object and record its bucket, tenant-scoped key,
   current version ID, and earlier version ID as sanitized identifiers.
2. Read the earlier version using an operator recovery role; the application
   task role must remain limited to its normal tenant-scoped object contract.
3. Recover by copying the selected earlier version to a new current version.
   Do not disable versioning, public-access blocking, or KMS encryption.
4. Run the document digest, scanner, tenant authorization, and ingestion checks.
5. Record the version transition and pass/fail result without retaining document
   content, object bodies, filenames, or secret values.

Any infected, corrupt, cross-tenant, or unverifiable object remains unavailable
and follows the application's stable failure mapping.
