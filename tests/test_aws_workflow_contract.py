from pathlib import Path


ROOT = Path(__file__).parents[1]
DEPLOY_WORKFLOW = ROOT / ".github" / "workflows" / "aws-deploy.yml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def test_deploy_workflow_keeps_oidc_and_concurrency_boundaries_explicit() -> None:
    workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    assert "id-token: write" in workflow
    assert "aws-actions/configure-aws-credentials@v5" in workflow
    assert "supportflow-aws-${{ inputs.environment || 'staging' }}" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "aws_access_key_id" not in workflow.lower()
    assert "aws_secret_access_key" not in workflow.lower()
    assert "MIGRATION_BACKUP_CONFIRMED" in workflow
    assert "refs/heads/main" in workflow
    assert "SUPPORTFLOW_OIDC_AUDIENCE" in workflow
    assert "SUPPORTFLOW_OIDC_SUBJECT" in workflow
    assert "ENVIRONMENT_NO_SELF_REVIEW" in workflow
    assert "backup_reference" in workflow
    assert "schema_compatibility_reference" in workflow
    assert "previous_release_image" in workflow
    assert "validate-migration-result" in workflow
    assert "SUPPORTFLOW_ECS_MIGRATION_CONTAINER_NAME" in workflow
    assert "previous-release.outputs.image_digest" in workflow
    assert "write-evidence" in workflow
    assert "week3_sensitive_scan.py" in workflow
    assert "steps.release-meta.outputs.release_started_at" in workflow
    assert "SUPPORTFLOW_CLOUDWATCH_LOG_GROUPS" in workflow
    assert "- 900" not in workflow
    assert (
        "Rollback targets have expected families and one matching immutable digest"
        in workflow
    )
    assert "Scan CloudWatch rollback logs" in workflow
    assert "smoke-status rolled_back" in workflow


def test_migration_failure_cannot_be_hidden_by_container_order() -> None:
    workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    assert "validate-migration-result" in workflow
    assert 'aws ecs describe-tasks --cluster "$SUPPORTFLOW_ECS_CLUSTER"' in workflow
    assert "containers[0].exitCode" not in workflow


def test_ci_is_callable_as_the_required_deployment_gate() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_call:" in workflow
    assert "name: Quality checks" in workflow
    assert "name: Container smoke test" in workflow
