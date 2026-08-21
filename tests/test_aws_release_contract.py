import json
from pathlib import Path

import pytest

from scripts.aws.release_contract import (
    ReleaseConfig,
    ReleaseContractError,
    assert_task_definition_digest,
    build_release_evidence,
    render_task_definition,
    validate_image_uri,
    write_release_evidence,
)


IMAGE = "123456789012.dkr.ecr.eu-central-1.amazonaws.com/supportflow@sha256:" + "a" * 64


def deployment_environment() -> dict[str, str]:
    return {
        "SUPPORTFLOW_DEPLOY_ENVIRONMENT": "staging",
        "AWS_REGION": "eu-central-1",
        "SUPPORTFLOW_AWS_ROLE_ARN": "arn:aws:iam::123456789012:role/supportflow-deploy",
        "SUPPORTFLOW_ECR_REPOSITORY": "supportflow",
        "SUPPORTFLOW_ECS_CLUSTER": "supportflow-staging",
        "SUPPORTFLOW_ECS_API_SERVICE": "supportflow-api",
        "SUPPORTFLOW_ECS_WORKER_SERVICE": "supportflow-worker",
        "SUPPORTFLOW_ECS_API_TASK_FAMILY": "supportflow-api",
        "SUPPORTFLOW_ECS_WORKER_TASK_FAMILY": "supportflow-worker",
        "SUPPORTFLOW_ECS_MIGRATION_TASK_FAMILY": "supportflow-migration",
        "SUPPORTFLOW_ECS_API_CONTAINER_NAME": "api",
        "SUPPORTFLOW_ECS_WORKER_CONTAINER_NAME": "worker",
        "SUPPORTFLOW_ECS_MIGRATION_CONTAINER_NAME": "migration",
        "SUPPORTFLOW_ECS_SUBNET_IDS": "subnet-abc123,subnet-def456",
        "SUPPORTFLOW_ECS_SECURITY_GROUP_IDS": "sg-abc123",
        "SUPPORTFLOW_ALB_BASE_URL": "https://staging.example.test",
        "SUPPORTFLOW_CLOUDWATCH_LOG_GROUP": "/supportflow/staging",
        "SUPPORTFLOW_MIGRATION_BACKUP_CONFIRMED": "true",
    }


def task_definition() -> dict[str, object]:
    return {
        "taskDefinitionArn": "arn:aws:ecs:eu-central-1:123456789012:task-definition/supportflow-api:4",
        "revision": 4,
        "status": "ACTIVE",
        "family": "supportflow-api",
        "containerDefinitions": [
            {"name": "api", "image": "old.example/supportflow:old", "essential": True},
            {"name": "clamd", "image": "clamav:approved", "essential": True},
        ],
    }


def test_release_config_requires_backup_confirmation_and_validates_non_secret_inputs() -> (
    None
):
    config = ReleaseConfig.from_env(deployment_environment())

    assert config.environment == "staging"
    assert config.subnet_ids == ("subnet-abc123", "subnet-def456")
    assert config.security_group_ids == ("sg-abc123",)

    invalid = deployment_environment()
    invalid["SUPPORTFLOW_MIGRATION_BACKUP_CONFIRMED"] = "false"
    with pytest.raises(ReleaseContractError, match="BACKUP_CONFIRMED"):
        ReleaseConfig.from_env(invalid)


@pytest.mark.parametrize(
    "value",
    [
        "123456789012.dkr.ecr.eu-central-1.amazonaws.com/supportflow:latest",
        "supportflow@sha256:short",
        "supportflow@sha256:" + "G" * 64,
    ],
)
def test_image_contract_rejects_mutable_or_malformed_images(value: str) -> None:
    with pytest.raises(ReleaseContractError, match="immutable|valid"):
        validate_image_uri(value)


def test_render_strips_aws_read_only_fields_and_changes_only_application_container(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.json"
    destination = tmp_path / "rendered.json"
    source.write_text(json.dumps(task_definition()), encoding="utf-8")

    render_task_definition(
        source,
        destination,
        image_uri=IMAGE,
        container_name="api",
    )

    rendered = json.loads(destination.read_text(encoding="utf-8"))
    assert "taskDefinitionArn" not in rendered
    assert "revision" not in rendered
    assert rendered["containerDefinitions"][0]["image"] == IMAGE
    assert rendered["containerDefinitions"][1]["image"] == "clamav:approved"
    assert_task_definition_digest(
        destination,
        image_uri=IMAGE,
        container_name="api",
    )


def test_render_requires_exactly_one_named_container(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    source.write_text(
        json.dumps({"containerDefinitions": [{"name": "other"}]}),
        encoding="utf-8",
    )

    with pytest.raises(ReleaseContractError, match="exactly one"):
        render_task_definition(
            source,
            tmp_path / "rendered.json",
            image_uri=IMAGE,
            container_name="api",
        )


def test_release_evidence_is_allowlisted_and_contains_digest_not_registry_url(
    tmp_path: Path,
) -> None:
    evidence = build_release_evidence(
        commit_sha="b" * 40,
        image_uri=IMAGE,
        environment="staging",
        migration_revision="0004_documents",
        api_task_definition="supportflow-api:7",
        worker_task_definition="supportflow-worker:8",
        smoke_status="passed",
    )
    output = tmp_path / "release-evidence.json"
    write_release_evidence(output, evidence)
    serialized = output.read_text(encoding="utf-8")

    assert "123456789012.dkr.ecr" not in serialized
    assert evidence["image_digest"] == "sha256:" + "a" * 64
    assert set(json.loads(serialized)) == set(evidence)
    with pytest.raises(ReleaseContractError, match="allowlisted"):
        write_release_evidence(output, {**evidence, "secret": "must-not-write"})
