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
    assert "write-evidence" in workflow
    assert "week3_sensitive_scan.py" in workflow


def test_ci_is_callable_as_the_required_deployment_gate() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_call:" in workflow
    assert "name: Quality checks" in workflow
    assert "name: Container smoke test" in workflow
