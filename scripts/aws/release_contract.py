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


class ReleaseContractError(ValueError):
    """Raised when a deployment input violates the reviewed handoff."""


@dataclass(frozen=True)
class ReleaseConfig:
    environment: str
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
    cloudwatch_log_group: str

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

        return cls(
            environment=environment,
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
            cloudwatch_log_group=required("SUPPORTFLOW_CLOUDWATCH_LOG_GROUP"),
        )


def _csv_ids(value: str, pattern: re.Pattern[str], label: str) -> tuple[str, ...]:
    items = tuple(item.strip() for item in value.split(",") if item.strip())
    if not items or any(pattern.fullmatch(item) is None for item in items):
        raise ReleaseContractError(f"invalid {label} list in deployment inputs")
    return items


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
    if isinstance(payload, dict) and isinstance(payload.get("taskDefinition"), dict):
        payload = payload["taskDefinition"]
    if not isinstance(payload, dict) or not isinstance(
        payload.get("containerDefinitions"), list
    ):
        raise ReleaseContractError("invalid ECS task definition payload")
    matches = [
        item
        for item in payload["containerDefinitions"]
        if isinstance(item, dict) and item.get("name") == container_name
    ]
    if len(matches) != 1:
        raise ReleaseContractError(
            f"ECS task definition must contain exactly one {container_name!r} container"
        )
    actual = str(matches[0].get("image", ""))
    actual_repository, actual_digest = validate_image_uri(actual)
    if (actual_repository, actual_digest) != (expected_repository, expected_digest):
        raise ReleaseContractError(
            f"{container_name} image digest does not match the release digest"
        )


def build_release_evidence(
    *,
    commit_sha: str,
    image_uri: str,
    environment: str,
    migration_revision: str,
    api_task_definition: str,
    worker_task_definition: str,
    smoke_status: str,
) -> dict[str, str]:
    """Build the only fields allowed in a persisted release evidence record."""

    if not re.fullmatch(r"[0-9a-f]{40}", commit_sha):
        raise ReleaseContractError("commit SHA must be a full 40-character value")
    _, digest = validate_image_uri(image_uri)
    if environment not in ALLOWED_ENVIRONMENTS:
        raise ReleaseContractError("unsupported evidence environment")
    if smoke_status not in {"passed", "rolled_back"}:
        raise ReleaseContractError("smoke status must be passed or rolled_back")
    return {
        "schema_version": "1",
        "commit_sha": commit_sha,
        "image_digest": digest,
        "environment": environment,
        "migration_revision": migration_revision,
        "api_task_definition": api_task_definition,
        "worker_task_definition": worker_task_definition,
        "smoke_status": smoke_status,
    }


def write_release_evidence(path: Path, evidence: Mapping[str, str]) -> None:
    allowed = {
        "schema_version",
        "commit_sha",
        "image_digest",
        "environment",
        "migration_revision",
        "api_task_definition",
        "worker_task_definition",
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

    evidence = subparsers.add_parser("write-evidence")
    evidence.add_argument("output", type=Path)
    evidence.add_argument("--commit-sha", required=True)
    evidence.add_argument("--image-uri", required=True)
    evidence.add_argument("--environment", required=True)
    evidence.add_argument("--migration-revision", required=True)
    evidence.add_argument("--api-task-definition", required=True)
    evidence.add_argument("--worker-task-definition", required=True)
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
    elif args.command == "write-evidence":
        evidence = build_release_evidence(
            commit_sha=args.commit_sha,
            image_uri=args.image_uri,
            environment=args.environment,
            migration_revision=args.migration_revision,
            api_task_definition=args.api_task_definition,
            worker_task_definition=args.worker_task_definition,
            smoke_status=args.smoke_status,
        )
        write_release_evidence(args.output, evidence)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ReleaseContractError as exc:
        raise SystemExit(f"deployment contract rejected: {exc}") from exc
