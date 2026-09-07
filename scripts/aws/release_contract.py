"""Pure release-contract checks used by the AWS deployment workflow.

This module deliberately does not call AWS.  It validates the non-secret handoff
from #37, renders an existing ECS task definition with one immutable image, and
creates an allowlisted evidence record.  AWS CLI calls remain in the workflow so
that OIDC credentials never enter application code or test fixtures.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from typing import Mapping, Sequence
from urllib.parse import urlsplit


ALLOWED_ENVIRONMENTS = {"staging", "production-demo"}
SHA256_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
AWS_ROLE_ARN = re.compile(r"^arn:aws:iam::[0-9]{12}:role/[A-Za-z0-9+=,.@_-]+$")
AWS_SUBNET_ID = re.compile(r"^subnet-[0-9a-f]+$")
AWS_SECURITY_GROUP_ID = re.compile(r"^sg-[0-9a-f]+$")
SAFE_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")
ISO_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class ReleaseContractError(ValueError):
    """Raised when a deployment input violates the reviewed handoff."""


@dataclass(frozen=True)
class ReleaseConfig:
    environment: str
    deployment_mode: str
    aws_region: str
    oidc_role_arn: str
    ecr_repository: str
    ecs_cluster: str
    api_service: str
    worker_service: str
    api_task_family: str
    worker_task_family: str
    migration_task_family: str
    api_container_name: str
    worker_container_name: str
    migration_container_name: str
    subnet_ids: tuple[str, ...]
    security_group_ids: tuple[str, ...]
    alb_base_url: str
    cloudwatch_log_groups: tuple[str, ...]
    github_repository: str
    allowed_deploy_ref: str
    oidc_audience: str
    oidc_subject: str
    environment_review_required: bool
    environment_no_self_review: bool
    backup_reference: str
    schema_compatibility_reference: str

    @classmethod
    def from_env(cls, values: Mapping[str, str]) -> "ReleaseConfig":
        def required(name: str) -> str:
            value = values.get(name, "").strip()
            if not value:
                raise ReleaseContractError(f"missing required deployment input: {name}")
            return value

        environment = required("SUPPORTFLOW_DEPLOY_ENVIRONMENT")
        if environment not in ALLOWED_ENVIRONMENTS:
            raise ReleaseContractError(
                "SUPPORTFLOW_DEPLOY_ENVIRONMENT must be staging or production-demo"
            )

        role_arn = required("SUPPORTFLOW_AWS_ROLE_ARN")
        if not AWS_ROLE_ARN.fullmatch(role_arn):
            raise ReleaseContractError(
                "SUPPORTFLOW_AWS_ROLE_ARN must be an IAM role ARN, not a credential"
            )

        repository = required("GITHUB_REPOSITORY")
        github_ref = required("GITHUB_REF")
        allowed_deploy_ref = required("SUPPORTFLOW_ALLOWED_DEPLOY_REF")
        if allowed_deploy_ref != "refs/heads/main" or github_ref != allowed_deploy_ref:
            raise ReleaseContractError(
                "deployment credentials are allowed only from refs/heads/main"
            )
        oidc_audience = required("SUPPORTFLOW_OIDC_AUDIENCE")
        if oidc_audience != "sts.amazonaws.com":
            raise ReleaseContractError(
                "SUPPORTFLOW_OIDC_AUDIENCE must be sts.amazonaws.com"
            )
        oidc_subject = required("SUPPORTFLOW_OIDC_SUBJECT")
        expected_subject = f"repo:{repository}:environment:{environment}"
        if oidc_subject != expected_subject:
            raise ReleaseContractError(
                "SUPPORTFLOW_OIDC_SUBJECT must bind this repository and environment"
            )
        if values.get("SUPPORTFLOW_ENVIRONMENT_REVIEW_REQUIRED", "").lower() != "true":
            raise ReleaseContractError(
                "SUPPORTFLOW_ENVIRONMENT_REVIEW_REQUIRED must be true"
            )
        if values.get("SUPPORTFLOW_ENVIRONMENT_NO_SELF_REVIEW", "").lower() != "true":
            raise ReleaseContractError(
                "SUPPORTFLOW_ENVIRONMENT_NO_SELF_REVIEW must be true"
            )

        subnet_ids = _csv_ids(
            required("SUPPORTFLOW_ECS_SUBNET_IDS"),
            AWS_SUBNET_ID,
            "subnet",
        )
        security_group_ids = _csv_ids(
            required("SUPPORTFLOW_ECS_SECURITY_GROUP_IDS"),
            AWS_SECURITY_GROUP_ID,
            "security-group",
        )
        alb_base_url = required("SUPPORTFLOW_ALB_BASE_URL").rstrip("/")
        parsed_url = urlsplit(alb_base_url)
        if parsed_url.scheme != "https" or not parsed_url.netloc:
            raise ReleaseContractError(
                "SUPPORTFLOW_ALB_BASE_URL must be an HTTPS origin"
            )

        if values.get("SUPPORTFLOW_MIGRATION_BACKUP_CONFIRMED", "").lower() != "true":
            raise ReleaseContractError(
                "SUPPORTFLOW_MIGRATION_BACKUP_CONFIRMED must be true before deploy"
            )
        backup_reference = _safe_reference(
            required("SUPPORTFLOW_BACKUP_REFERENCE"), "SUPPORTFLOW_BACKUP_REFERENCE"
        )
        schema_compatibility_reference = _safe_reference(
            required("SUPPORTFLOW_SCHEMA_COMPATIBILITY_REFERENCE"),
            "SUPPORTFLOW_SCHEMA_COMPATIBILITY_REFERENCE",
        )
        for name in (
            "SUPPORTFLOW_SMOKE_ORGANIZATION_ID",
            "SUPPORTFLOW_SMOKE_EMAIL",
            "SUPPORTFLOW_SMOKE_PASSWORD",
            "SUPPORTFLOW_SMOKE_INITIAL_BODY",
            "SUPPORTFLOW_SMOKE_FOLLOWUP_BODY",
        ):
            required(name)
        operation = values.get("SUPPORTFLOW_RELEASE_OPERATION", "deploy").strip()
        if operation not in {"deploy", "rollback"}:
            raise ReleaseContractError("SUPPORTFLOW_RELEASE_OPERATION is invalid")
        deployment_mode = values.get("SUPPORTFLOW_DEPLOYMENT_MODE", "").strip()
        if deployment_mode not in {"bootstrap", "upgrade"}:
            raise ReleaseContractError(
                "SUPPORTFLOW_DEPLOYMENT_MODE must be bootstrap or upgrade"
            )
        if operation == "rollback" and deployment_mode != "upgrade":
            raise ReleaseContractError("rollback requires upgrade deployment mode")
        if operation == "rollback":
            validate_image_uri(required("SUPPORTFLOW_PREVIOUS_RELEASE_IMAGE"))

        cloudwatch_log_groups = _csv_values(
            required("SUPPORTFLOW_CLOUDWATCH_LOG_GROUPS"),
            "CloudWatch log group",
        )

        return cls(
            environment=environment,
            deployment_mode=deployment_mode,
            aws_region=required("AWS_REGION"),
            oidc_role_arn=role_arn,
            ecr_repository=required("SUPPORTFLOW_ECR_REPOSITORY").strip("/"),
            ecs_cluster=required("SUPPORTFLOW_ECS_CLUSTER"),
            api_service=required("SUPPORTFLOW_ECS_API_SERVICE"),
            worker_service=required("SUPPORTFLOW_ECS_WORKER_SERVICE"),
            api_task_family=required("SUPPORTFLOW_ECS_API_TASK_FAMILY"),
            worker_task_family=required("SUPPORTFLOW_ECS_WORKER_TASK_FAMILY"),
            migration_task_family=required("SUPPORTFLOW_ECS_MIGRATION_TASK_FAMILY"),
            api_container_name=required("SUPPORTFLOW_ECS_API_CONTAINER_NAME"),
            worker_container_name=required("SUPPORTFLOW_ECS_WORKER_CONTAINER_NAME"),
            migration_container_name=required(
                "SUPPORTFLOW_ECS_MIGRATION_CONTAINER_NAME"
            ),
            subnet_ids=subnet_ids,
            security_group_ids=security_group_ids,
            alb_base_url=alb_base_url,
            cloudwatch_log_groups=cloudwatch_log_groups,
            github_repository=repository,
            allowed_deploy_ref=allowed_deploy_ref,
            oidc_audience=oidc_audience,
            oidc_subject=oidc_subject,
            environment_review_required=True,
            environment_no_self_review=True,
            backup_reference=backup_reference,
            schema_compatibility_reference=schema_compatibility_reference,
        )


def _csv_ids(value: str, pattern: re.Pattern[str], label: str) -> tuple[str, ...]:
    items = tuple(item.strip() for item in value.split(",") if item.strip())
    if not items or any(pattern.fullmatch(item) is None for item in items):
        raise ReleaseContractError(f"invalid {label} list in deployment inputs")
    return items


def _csv_values(value: str, label: str) -> tuple[str, ...]:
    items = tuple(item.strip() for item in value.split(",") if item.strip())
    if not items:
        raise ReleaseContractError(f"missing {label} list in deployment inputs")
    return items


def _safe_reference(value: str, name: str) -> str:
    if SAFE_REFERENCE.fullmatch(value) is None:
        raise ReleaseContractError(f"{name} must be a non-secret allowlisted reference")
    return value


def validate_image_uri(image_uri: str) -> tuple[str, str]:
    """Return repository and digest, requiring an immutable ECR image URI."""

    value = image_uri.strip()
    if "@" not in value:
        raise ReleaseContractError("release image must use an immutable @sha256 digest")
    repository, digest = value.rsplit("@", 1)
    if not repository or SHA256_DIGEST.fullmatch(digest) is None:
        raise ReleaseContractError("release image must contain a valid sha256 digest")
    return repository, digest


def _task_definition_payload(source: Mapping[str, object]) -> dict[str, object]:
    payload = dict(source)
    for field in (
        "taskDefinitionArn",
        "revision",
        "status",
        "requiresAttributes",
        "compatibilities",
        "registeredAt",
        "registeredBy",
    ):
        payload.pop(field, None)
    return payload


def render_task_definition(
    source_path: Path,
    destination_path: Path,
    *,
    image_uri: str,
    container_name: str,
) -> None:
    """Render one application image into a reviewed ECS task definition."""

    validate_image_uri(image_uri)
    try:
        source = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseContractError(
            f"cannot read ECS task definition: {source_path}"
        ) from exc
    if not isinstance(source, dict):
        raise ReleaseContractError("ECS task definition must be a JSON object")
    if isinstance(source.get("taskDefinition"), dict):
        source = source["taskDefinition"]
    payload = _task_definition_payload(source)
    containers = payload.get("containerDefinitions")
    if not isinstance(containers, list):
        raise ReleaseContractError("ECS task definition has no containerDefinitions")
    matches = [
        item
        for item in containers
        if isinstance(item, dict) and item.get("name") == container_name
    ]
    if len(matches) != 1:
        raise ReleaseContractError(
            f"ECS task definition must contain exactly one {container_name!r} container"
        )
    matches[0]["image"] = image_uri
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    destination_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def assert_task_definition_digest(
    task_definition_path: Path,
    *,
    image_uri: str,
    container_name: str,
) -> None:
    """Require the registered task definition's application container to match."""

    expected_repository, expected_digest = validate_image_uri(image_uri)
    try:
        payload = json.loads(task_definition_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseContractError("cannot read rendered ECS task definition") from exc
    actual = task_definition_image_from_payload(payload, container_name)
    actual_repository, actual_digest = validate_image_uri(actual)
    if (actual_repository, actual_digest) != (expected_repository, expected_digest):
        raise ReleaseContractError(
            f"{container_name} image digest does not match the release digest"
        )


def task_definition_image_from_payload(
    payload: Mapping[str, object], container_name: str
) -> str:
    """Return one named container image, rejecting missing/ambiguous containers."""

    nested = payload.get("taskDefinition")
    if isinstance(nested, dict):
        payload = nested
    containers = payload.get("containerDefinitions")
    if not isinstance(containers, list):
        raise ReleaseContractError("invalid ECS task definition payload")
    matches = [
        item
        for item in containers
        if isinstance(item, dict) and item.get("name") == container_name
    ]
    if len(matches) != 1 or not isinstance(matches[0].get("image"), str):
        raise ReleaseContractError(
            f"ECS task definition must contain exactly one {container_name!r} container"
        )
    return matches[0]["image"]


def _load_json(path: Path, label: str) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseContractError(f"cannot read {label}: {path}") from exc
    if not isinstance(payload, dict):
        raise ReleaseContractError(f"{label} must be a JSON object")
    return payload


def run_task_arn(payload: Mapping[str, object]) -> str:
    """Validate ECS run-task response and return its sole task ARN."""

    failures = payload.get("failures")
    if isinstance(failures, list) and failures:
        raise ReleaseContractError("ECS migration run-task returned a failure")
    tasks = payload.get("tasks")
    if not isinstance(tasks, list) or len(tasks) != 1 or not isinstance(tasks[0], dict):
        raise ReleaseContractError(
            "ECS migration run-task must return exactly one task"
        )
    arn = tasks[0].get("taskArn")
    if not isinstance(arn, str) or not arn:
        raise ReleaseContractError("ECS migration run-task did not return a task ARN")
    return arn


def validate_migration_task_result(
    payload: Mapping[str, object], container_name: str
) -> None:
    """Require the named migration container to stop successfully."""

    arn = run_task_arn(payload)
    del arn
    tasks = payload["tasks"]
    assert isinstance(tasks, list) and isinstance(tasks[0], dict)
    task = tasks[0]
    if task.get("lastStatus") != "STOPPED":
        raise ReleaseContractError("migration task did not reach STOPPED")
    if task.get("stopCode") != "EssentialContainerExited":
        raise ReleaseContractError("migration task stopped for a non-success reason")
    stopped_reason = str(task.get("stoppedReason", "")).lower()
    if not stopped_reason or any(
        marker in stopped_reason for marker in ("failed", "unable", "timeout", "error")
    ):
        raise ReleaseContractError("migration task has a non-success stop reason")
    containers = task.get("containers")
    if not isinstance(containers, list):
        raise ReleaseContractError("migration task has no container results")
    matches = [
        item
        for item in containers
        if isinstance(item, dict) and item.get("name") == container_name
    ]
    if len(matches) != 1:
        raise ReleaseContractError(
            f"migration result must contain exactly one {container_name!r} container"
        )
    if matches[0].get("lastStatus") != "STOPPED" or matches[0].get("exitCode") != 0:
        raise ReleaseContractError(
            "named migration container did not exit successfully"
        )


def build_release_evidence(
    *,
    commit_sha: str,
    image_uri: str,
    environment: str,
    deployment_mode: str,
    operation: str,
    release_started_at: str,
    backup_reference: str,
    schema_compatibility_reference: str,
    migration_revision: str,
    api_task_definition: str,
    worker_task_definition: str,
    previous_api_task_definition: str,
    previous_worker_task_definition: str,
    previous_image_digest: str,
    smoke_status: str,
) -> dict[str, str]:
    """Build the only fields allowed in a persisted release evidence record."""

    if not re.fullmatch(r"[0-9a-f]{40}", commit_sha):
        raise ReleaseContractError("commit SHA must be a full 40-character value")
    _, digest = validate_image_uri(image_uri)
    if environment not in ALLOWED_ENVIRONMENTS:
        raise ReleaseContractError("unsupported evidence environment")
    if deployment_mode not in {"bootstrap", "upgrade"}:
        raise ReleaseContractError("unsupported deployment mode")
    if operation not in {"deploy", "rollback"}:
        raise ReleaseContractError("unsupported release operation")
    if deployment_mode == "bootstrap" and operation != "deploy":
        raise ReleaseContractError("bootstrap evidence can only describe deploy")
    if operation == "deploy" and smoke_status != "passed":
        raise ReleaseContractError("deploy evidence requires passed smoke status")
    if operation == "rollback" and (
        deployment_mode != "upgrade" or smoke_status != "rolled_back"
    ):
        raise ReleaseContractError(
            "rollback evidence requires upgrade mode and rolled_back status"
        )
    if ISO_UTC.fullmatch(release_started_at) is None:
        raise ReleaseContractError("release start must be an ISO UTC timestamp")
    _safe_reference(backup_reference, "backup_reference")
    _safe_reference(schema_compatibility_reference, "schema_compatibility_reference")
    for name, value in (
        ("migration_revision", migration_revision),
        ("api_task_definition", api_task_definition),
        ("worker_task_definition", worker_task_definition),
        ("previous_api_task_definition", previous_api_task_definition),
        ("previous_worker_task_definition", previous_worker_task_definition),
    ):
        _safe_reference(value, name)
    if deployment_mode == "bootstrap":
        if (
            previous_image_digest != "none"
            or previous_api_task_definition != "none"
            or previous_worker_task_definition != "none"
        ):
            raise ReleaseContractError(
                "bootstrap evidence must record absent previous release"
            )
    elif SHA256_DIGEST.fullmatch(previous_image_digest) is None:
        raise ReleaseContractError(
            "upgrade evidence must record a previous sha256 digest"
        )
    if smoke_status not in {"passed", "rolled_back"}:
        raise ReleaseContractError("smoke status must be passed or rolled_back")
    return {
        "schema_version": "1",
        "commit_sha": commit_sha,
        "image_digest": digest,
        "environment": environment,
        "deployment_mode": deployment_mode,
        "operation": operation,
        "release_started_at": release_started_at,
        "backup_reference": backup_reference,
        "schema_compatibility_reference": schema_compatibility_reference,
        "migration_revision": migration_revision,
        "api_task_definition": api_task_definition,
        "worker_task_definition": worker_task_definition,
        "previous_api_task_definition": previous_api_task_definition,
        "previous_worker_task_definition": previous_worker_task_definition,
        "previous_image_digest": previous_image_digest,
        "smoke_status": smoke_status,
    }


def write_release_evidence(path: Path, evidence: Mapping[str, str]) -> None:
    allowed = {
        "schema_version",
        "commit_sha",
        "image_digest",
        "environment",
        "deployment_mode",
        "operation",
        "release_started_at",
        "backup_reference",
        "schema_compatibility_reference",
        "migration_revision",
        "api_task_definition",
        "worker_task_definition",
        "previous_api_task_definition",
        "previous_worker_task_definition",
        "previous_image_digest",
        "smoke_status",
    }
    if set(evidence) != allowed:
        raise ReleaseContractError("release evidence contains a non-allowlisted field")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(evidence), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate-config")

    render = subparsers.add_parser("render-task-definition")
    render.add_argument("source", type=Path)
    render.add_argument("destination", type=Path)
    render.add_argument("--image-uri", required=True)
    render.add_argument("--container-name", required=True)

    parity = subparsers.add_parser("assert-task-definition-digest")
    parity.add_argument("task_definition", type=Path)
    parity.add_argument("--image-uri", required=True)
    parity.add_argument("--container-name", required=True)

    run_result = subparsers.add_parser("validate-run-task-result")
    run_result.add_argument("result", type=Path)

    migration_result = subparsers.add_parser("validate-migration-result")
    migration_result.add_argument("result", type=Path)
    migration_result.add_argument("--container-name", required=True)

    extract_digest = subparsers.add_parser("extract-task-definition-digest")
    extract_digest.add_argument("task_definition", type=Path)
    extract_digest.add_argument("--container-name", required=True)

    evidence = subparsers.add_parser("write-evidence")
    evidence.add_argument("output", type=Path)
    evidence.add_argument("--commit-sha", required=True)
    evidence.add_argument("--image-uri", required=True)
    evidence.add_argument("--environment", required=True)
    evidence.add_argument("--deployment-mode", required=True)
    evidence.add_argument("--operation", required=True)
    evidence.add_argument("--release-started-at", required=True)
    evidence.add_argument("--backup-reference", required=True)
    evidence.add_argument("--schema-compatibility-reference", required=True)
    evidence.add_argument("--migration-revision", required=True)
    evidence.add_argument("--api-task-definition", required=True)
    evidence.add_argument("--worker-task-definition", required=True)
    evidence.add_argument("--previous-api-task-definition", required=True)
    evidence.add_argument("--previous-worker-task-definition", required=True)
    evidence.add_argument("--previous-image-digest", required=True)
    evidence.add_argument("--smoke-status", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "validate-config":
        config = ReleaseConfig.from_env(os.environ)
        print(
            f"validated deployment contract environment={config.environment} "
            f"region={config.aws_region} subnet_count={len(config.subnet_ids)}"
        )
    elif args.command == "render-task-definition":
        render_task_definition(
            args.source,
            args.destination,
            image_uri=args.image_uri,
            container_name=args.container_name,
        )
    elif args.command == "assert-task-definition-digest":
        assert_task_definition_digest(
            args.task_definition,
            image_uri=args.image_uri,
            container_name=args.container_name,
        )
    elif args.command == "validate-run-task-result":
        run_task_arn(_load_json(args.result, "ECS run-task result"))
    elif args.command == "validate-migration-result":
        validate_migration_task_result(
            _load_json(args.result, "ECS migration task result"),
            args.container_name,
        )
    elif args.command == "extract-task-definition-digest":
        _, digest = validate_image_uri(
            task_definition_image_from_payload(
                _load_json(args.task_definition, "ECS task definition"),
                args.container_name,
            )
        )
        print(digest)
    elif args.command == "write-evidence":
        evidence = build_release_evidence(
            commit_sha=args.commit_sha,
            image_uri=args.image_uri,
            environment=args.environment,
            deployment_mode=args.deployment_mode,
            operation=args.operation,
            release_started_at=args.release_started_at,
            backup_reference=args.backup_reference,
            schema_compatibility_reference=args.schema_compatibility_reference,
            migration_revision=args.migration_revision,
            api_task_definition=args.api_task_definition,
            worker_task_definition=args.worker_task_definition,
            previous_api_task_definition=args.previous_api_task_definition,
            previous_worker_task_definition=args.previous_worker_task_definition,
            previous_image_digest=args.previous_image_digest,
            smoke_status=args.smoke_status,
        )
        write_release_evidence(args.output, evidence)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ReleaseContractError as exc:
        raise SystemExit(f"deployment contract rejected: {exc}") from exc
