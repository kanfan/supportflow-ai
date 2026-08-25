from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).parents[1]
DEPLOY_WORKFLOW = ROOT / ".github" / "workflows" / "aws-deploy.yml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def _workflow() -> dict[Any, Any]:
    parsed = yaml.safe_load(DEPLOY_WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    return parsed


def _jobs() -> dict[str, dict[str, Any]]:
    jobs = _workflow()["jobs"]
    assert isinstance(jobs, dict)
    return jobs


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    steps = job.get("steps")
    assert isinstance(steps, list)
    return [step for step in steps if isinstance(step, dict)]


def test_workflow_has_semantic_job_permissions_and_dependencies() -> None:
    jobs = _jobs()

    assert jobs["quality-gate"]["uses"] == "./.github/workflows/ci.yml"
    assert jobs["validate-contract"]["needs"] == "quality-gate"
    assert jobs["deploy"]["needs"] == "validate-contract"
    assert jobs["rollback"]["needs"] == "validate-contract"
    assert (
        jobs["deploy"]["if"]
        == "inputs.operation == 'deploy' && github.ref == 'refs/heads/main'"
    )
    assert (
        jobs["rollback"]["if"]
        == "inputs.operation == 'rollback' && github.ref == 'refs/heads/main'"
    )

    for name, job in jobs.items():
        permissions = job.get("permissions", {})
        assert isinstance(permissions, dict)
        if name in {"deploy", "rollback"}:
            assert permissions.get("id-token") == "write"
        else:
            assert "id-token" not in permissions

    assert _workflow()["concurrency"] == {
        "group": "supportflow-aws-${{ inputs.environment || 'staging' }}",
        "cancel-in-progress": False,
    }
    trigger = _workflow().get("on", _workflow().get(True))
    assert isinstance(trigger, dict)
    inputs = trigger["workflow_dispatch"]["inputs"]
    assert inputs["deployment_mode"]["options"] == ["bootstrap", "upgrade"]
    assert inputs["backup_reference"]["required"] is True
    assert inputs["schema_compatibility_reference"]["required"] is True


def test_deploy_steps_preserve_release_order_and_bootstrap_mode() -> None:
    deploy = _jobs()["deploy"]
    steps = _steps(deploy)
    names = [step.get("name") for step in steps]

    ordered = [
        "Configure AWS credentials through GitHub OIDC",
        "Build and push one commit-tagged image",
        "Run migration task before service promotion",
        "Promote API and worker using the same release image",
        "Verify ALB readiness and run synthetic application smoke",
        "Scan CloudWatch release logs without echoing them",
        "Write sanitized release evidence",
    ]
    positions = [names.index(name) for name in ordered]
    assert positions == sorted(positions)

    capture = next(step for step in steps if step.get("id") == "previous-release")
    assert capture["if"] == "inputs.deployment_mode == 'upgrade'"
    evidence = next(
        step for step in steps if step.get("name") == "Write sanitized release evidence"
    )
    assert "--deployment-mode" in evidence["run"]
    assert "PREVIOUS_IMAGE_DIGEST" in evidence["env"]


def test_rollback_structure_requires_digest_and_never_downgrades_database() -> None:
    rollback = _jobs()["rollback"]
    steps = _steps(rollback)
    names = [step.get("name") for step in steps]
    assert names.index("Validate previous task-definition families") < names.index(
        "Restore previous API and worker revisions"
    )
    assert names.index("Restore previous API and worker revisions") < names.index(
        "Scan CloudWatch rollback logs without echoing them"
    )
    assert names.index(
        "Scan CloudWatch rollback logs without echoing them"
    ) < names.index("Write sanitized rollback evidence")
    validation = next(
        step
        for step in steps
        if step.get("name") == "Validate previous task-definition families"
    )
    assert "assert-task-definition-digest" in validation["run"]
    restore = next(
        step
        for step in steps
        if step.get("name") == "Restore previous API and worker revisions"
    )
    assert "alembic downgrade" not in restore["run"].lower()


def test_ci_is_callable_as_the_required_deployment_gate() -> None:
    workflow = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict)
    trigger = workflow.get("on", workflow.get(True))
    assert isinstance(trigger, dict)
    assert "workflow_call" in trigger
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    assert "quality" in jobs
    assert "container-smoke" in jobs
