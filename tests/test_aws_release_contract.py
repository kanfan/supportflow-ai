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
        "SUPPORTFLOW_CLOUDWATCH_LOG_GROUPS": "/supportflow/staging,/supportflow/worker",
        "SUPPORTFLOW_MIGRATION_BACKUP_CONFIRMED": "true",
        "GITHUB_REPOSITORY": "kanfan/supportflow-ai",
        "GITHUB_REF": "refs/heads/main",
        "SUPPORTFLOW_RELEASE_OPERATION": "deploy",
        "SUPPORTFLOW_DEPLOYMENT_MODE": "upgrade",
        "SUPPORTFLOW_ALLOWED_DEPLOY_REF": "refs/heads/main",
        "SUPPORTFLOW_OIDC_AUDIENCE": "sts.amazonaws.com",
        "SUPPORTFLOW_OIDC_SUBJECT": "repo:kanfan/supportflow-ai:environment:staging",
        "SUPPORTFLOW_ENVIRONMENT_REVIEW_REQUIRED": "true",
        "SUPPORTFLOW_ENVIRONMENT_NO_SELF_REVIEW": "true",
        "SUPPORTFLOW_BACKUP_REFERENCE": "backup:staging:2026-08-24T1200Z",
        "SUPPORTFLOW_SCHEMA_COMPATIBILITY_REFERENCE": "schema:0004_documents",
        "SUPPORTFLOW_SMOKE_ORGANIZATION_ID": "00000000-0000-0000-0000-000000000001",
        "SUPPORTFLOW_SMOKE_EMAIL": "smoke@example.test",
        "SUPPORTFLOW_SMOKE_PASSWORD": "not-a-real-password",
        "SUPPORTFLOW_SMOKE_INITIAL_BODY": "initial smoke canary",
        "SUPPORTFLOW_SMOKE_FOLLOWUP_BODY": "followup smoke canary",
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
    assert config.deployment_mode == "upgrade"
    assert config.subnet_ids == ("subnet-abc123", "subnet-def456")
    assert config.security_group_ids == ("sg-abc123",)

    invalid = deployment_environment()
    invalid["SUPPORTFLOW_MIGRATION_BACKUP_CONFIRMED"] = "false"
    with pytest.raises(ReleaseContractError, match="BACKUP_CONFIRMED"):
        ReleaseConfig.from_env(invalid)


def test_bootstrap_config_does_not_require_a_previous_release() -> None:
    values = deployment_environment()
    values["SUPPORTFLOW_DEPLOYMENT_MODE"] = "bootstrap"
    values.pop("SUPPORTFLOW_PREVIOUS_RELEASE_IMAGE", None)

    config = ReleaseConfig.from_env(values)

    assert config.deployment_mode == "bootstrap"


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
        deployment_mode="upgrade",
        operation="deploy",
        release_started_at="2026-08-24T12:00:00Z",
        backup_reference="backup:staging:2026-08-24T1200Z",
        schema_compatibility_reference="schema:0004_documents",
        migration_revision="0004_documents",
        api_task_definition="supportflow-api:7",
        worker_task_definition="supportflow-worker:8",
        previous_api_task_definition="supportflow-api:6",
        previous_worker_task_definition="supportflow-worker:6",
        previous_image_digest="sha256:" + "b" * 64,
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


def test_bootstrap_evidence_records_absent_previous_release() -> None:
    evidence = build_release_evidence(
        commit_sha="c" * 40,
        image_uri=IMAGE,
        environment="staging",
        deployment_mode="bootstrap",
        operation="deploy",
        release_started_at="2026-08-25T12:00:00Z",
        backup_reference="fresh-env:2026-08-25",
        schema_compatibility_reference="schema:0004_documents",
        migration_revision="0004_documents",
        api_task_definition="supportflow-api:1",
        worker_task_definition="supportflow-worker:1",
        previous_api_task_definition="none",
        previous_worker_task_definition="none",
        previous_image_digest="none",
        smoke_status="passed",
    )

    assert evidence["deployment_mode"] == "bootstrap"
    assert evidence["previous_image_digest"] == "none"


def test_migration_result_uses_named_container_even_when_sidecar_is_first() -> None:
    from scripts.aws.release_contract import validate_migration_task_result

    validate_migration_task_result(
        {
            "tasks": [
                {
                    "taskArn": "arn:aws:ecs:task/migration",
                    "lastStatus": "STOPPED",
                    "stopCode": "EssentialContainerExited",
                    "stoppedReason": "Essential container in task exited",
                    "containers": [
                        {"name": "clamd", "lastStatus": "STOPPED", "exitCode": 0},
                        {"name": "migration", "lastStatus": "STOPPED", "exitCode": 0},
                    ],
                }
            ],
            "failures": [],
        },
        "migration",
    )


def test_migration_result_rejects_named_container_failure() -> None:
    from scripts.aws.release_contract import validate_migration_task_result

    with pytest.raises(ReleaseContractError, match="did not exit successfully"):
        validate_migration_task_result(
            {
                "tasks": [
                    {
                        "taskArn": "arn:aws:ecs:task/migration",
                        "lastStatus": "STOPPED",
                        "stopCode": "EssentialContainerExited",
                        "stoppedReason": "Essential container in task exited",
                        "containers": [
                            {"name": "clamd", "lastStatus": "STOPPED", "exitCode": 0},
                            {
                                "name": "migration",
                                "lastStatus": "STOPPED",
                                "exitCode": 1,
                            },
                        ],
                    }
                ],
                "failures": [],
            },
            "migration",
        )
