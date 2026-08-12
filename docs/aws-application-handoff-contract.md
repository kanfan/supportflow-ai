# Week 5 AWS application handoff contract

Status: review candidate for Issue #37

This document freezes the non-secret interfaces needed for the remaining
Issue #38 application work. It does not claim that an AWS account, Terraform
state, or live staging resources already exist. Those claims require reviewed
Terraform plans and post-apply evidence.

## Concrete malware scanner

### Selection and topology

The staging scanner is the official ClamAV `clamd` image, mirrored into a
private ECR repository and referenced by an immutable digest. The selected
feature release and digest are release inputs; mutable `latest` or `stable`
tags are not accepted as deployment evidence.

`clamd` runs as an essential sidecar in the worker ECS/Fargate task. It listens
only on `127.0.0.1:3310`. The worker sends the already bounded document stream
with the framed ClamD `INSTREAM` protocol. The API task does not contain the
scanner, and no scanner port is registered in a target group or allowed by a
security-group ingress rule.

This preserves the existing application boundary:

```text
DocumentSafetyScanner.scan(stream) -> clean | infected | unavailable
```

The TCP protocol has no authentication or encryption. Loopback-only binding
and same-task isolation are therefore mandatory, not optional hardening. The
worker depends on the scanner container reaching `HEALTHY`; the ECS health
check sends a framed `PING` and requires `PONG`.

### Runtime limits and result mapping

| Contract | Staging value |
| --- | --- |
| Maximum uploaded/streamed object | 10 MiB |
| Stream chunk | 64 KiB |
| Connect timeout | 2 seconds |
| Complete scan timeout | 60 seconds |
| Scanner concurrency | 2 ClamD threads; one in-flight scan per worker child |
| Client retries | Existing bounded Celery retry policy; no retry inside one scan |
| Signature freshness gate | Definitions no older than 24 hours |

The adapter maps only allowlisted protocol outcomes:

- `stream: OK` becomes `clean`.
- A syntactically valid `FOUND` response becomes `infected`.
- timeout, connection failure, size-limit response, stale definitions,
  malformed/unexpected replies, or any ClamD `ERROR` becomes `unavailable`.

`unavailable` fails closed and remains retryable. Parsing never begins unless
the result is `clean`. Provider replies, signature names, document bytes,
filenames, S3 keys, endpoints, and socket exceptions must not appear in API
responses, audit metadata, Celery results, or logs. Logs use only the existing
stable scanner error category and safe document/version identifiers.

The mirrored scanner image starts with official definitions and uses
`freshclam` through the private-subnet NAT path. A failed freshness gate makes
the sidecar unhealthy. The scanner receives no AWS application task role and
no SupportFlow secrets.

### Scanner cost boundary

ClamAV documents a 4 GiB preferred container allocation. The staging worker
task budget is therefore 1 vCPU and 5 GB total: 0.5 vCPU/1 GB for the worker
and 0.5 vCPU/4 GB for the scanner. At the public `eu-central-1` Linux/x86
on-demand rates observed on 2026-08-12 (`$0.04656` per vCPU-hour and
`$0.00511` per GB-hour), that complete task is approximately `$52.64` for 730
hours or `$17.31` at eight hours per day for 30 days, before logs and network
usage.

The staging environment must therefore run only for a planned portfolio
verification window, follow the USD 10/25/50 early notifications and USD 120
emergency account ceiling, and be destroyed within 24 hours after acceptance
evidence is captured. The 48-hour idle teardown rule remains a backstop. A
reviewed plan must recalculate the whole-environment estimate before apply.

## RDS PostgreSQL connection contract

| Application setting/output | Source | Classification |
| --- | --- | --- |
| `SUPPORTFLOW_DATABASE_URL` | Secrets Manager secret injected by ECS | Secret |
| RDS hostname and port | Terraform outputs and URL secret composition | Non-secret |
| Database secret ARN/name | Terraform output | Non-secret reference |
| `SUPPORTFLOW_DATABASE_SSL_ROOT_CERT_PATH` | ECS environment value | Non-secret |
| CA path | `/app/certs/aws-rds/eu-central-1-bundle.pem` | Non-secret |

The database URL uses the RDS endpoint hostname and the
`postgresql+psycopg://` scheme. It contains neither `sslmode` nor
`sslrootcert`; the application forces `verify-full` and the explicit CA path.
The password is never a Terraform output, task-definition environment value,
GitHub variable, or committed file.

The immutable SupportFlow image contains the public AWS
`eu-central-1-bundle.pem` at the path above. The build downloads it only from
the official RDS trust-store endpoint, verifies a review-pinned SHA-256, and
records that checksum in release evidence. Certificate rotation requires a new
reviewed image and normal ECS deployment. The file is not delivered through
Secrets Manager because it is public trust material.

The RDS instance remains private, accepts port 5432 only from the ECS task
security group, and sets `rds.force_ssl=1`. API, worker, migration, readiness,
and staging smoke use the same hostname, secret URL, CA file, and image digest.
Live acceptance must prove `verify-full` succeeds with the expected hostname
and fails with a wrong hostname or untrusted CA.

## Inputs #37 must publish after apply

The platform handoff publishes only sanitized values:

- AWS account ID, region, environment, and mandatory tags;
- RDS endpoint/port, parameter-group SSL enforcement, database secret ARN/name,
  and CA bundle path/checksum;
- S3 bucket name/ARN, KMS key ARN, and application prefix contract;
- ElastiCache primary endpoint/port, TLS/auth status, and secret ARN/name;
- API/worker/migration task role ARNs and exact least-privilege boundaries;
- ECR repository URLs for SupportFlow and the mirrored scanner, plus the
  approved scanner digest;
- ECS cluster/service/task-family names, ALB target/readiness contract, log
  groups, alarms, budget evidence, and Terraform state location;
- deploy, rollback, restore, cost-response, and destroy commands.

No credential, token, database password, Redis authentication token, secret
value, Terraform state, document content, or customer data belongs in this
handoff.

## Gates before live integration

1. Emir reviews and accepts this contract without receiving AWS credentials.
2. Terraform plan proves private network, IAM, encryption, budget, and teardown
   boundaries and contains no secret values.
3. Eray performs the only persistent apply and publishes sanitized outputs.
4. Issue #38 implements and contract-tests the ClamD adapter locally.
5. Issue #39 deploys one immutable SupportFlow digest plus the separately
   approved scanner digest.
6. Issue #40 records live RDS TLS, scanner, S3, ElastiCache, rollback, restore,
   log-redaction, and cost evidence.

## Primary references

- [ClamD protocol and INSTREAM](https://docs.clamav.net/manual/Usage/ClamdProtocol.html)
- [Official ClamAV container guidance](https://docs.clamav.net/manual/Installing/Docker.html)
- [ECS/Fargate sidecar isolation](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/fargate-security-considerations.html)
- [RDS PostgreSQL certificate verification](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/PostgreSQL.Concepts.General.SSL.html)
- [RDS regional CA bundles](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/UsingWithRDS.SSL.html)
- [AWS Fargate pricing](https://aws.amazon.com/fargate/pricing/)
