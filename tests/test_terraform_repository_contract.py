from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).parents[1]
TERRAFORM_CI = ROOT / ".github" / "workflows" / "terraform-ci.yml"


def _workflow() -> dict[Any, Any]:
    parsed = yaml.safe_load(TERRAFORM_CI.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    return parsed


def test_terraform_ci_has_no_cloud_credentials_or_write_permissions() -> None:
    workflow = _workflow()
    assert workflow["permissions"] == {"contents": "read"}

    job = workflow["jobs"]["validate"]
    assert job["strategy"]["matrix"]["stack"] == ["bootstrap", "foundation"]
    steps = job["steps"]
    commands = "\n".join(
        str(step.get("run", "")) for step in steps if isinstance(step, dict)
    )
    assert "terraform init -backend=false" in commands
    assert "terraform validate" in commands
    assert "terraform test" in commands
    assert "terraform plan" not in commands
    assert "terraform apply" not in commands
    assert "id-token" not in workflow["permissions"]


def test_terraform_runtime_artifacts_are_ignored_but_provider_locks_are_not() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in (
        "**/.terraform/",
        "*.tfstate",
        "*.tfstate.*",
        "*.tfplan",
        "**/backend.hcl",
        "**/backend_override.tf",
        "**/terraform.tfvars",
    ):
        assert pattern in gitignore
    assert ".terraform.lock.hcl" not in gitignore


def test_committed_terraform_contains_no_long_lived_credential_fields() -> None:
    forbidden = (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "access_key =",
        "secret_key =",
    )
    for path in (ROOT / "infra" / "aws").rglob("*"):
        if path.is_file() and path.suffix in {".tf", ".hcl", ".example"}:
            content = path.read_text(encoding="utf-8")
            assert all(value not in content for value in forbidden), path


def test_oidc_and_iam_foundation_stays_exact_and_fail_closed() -> None:
    locals_hcl = (ROOT / "infra" / "aws" / "foundation" / "locals.tf").read_text(
        encoding="utf-8"
    )
    variables_hcl = (ROOT / "infra" / "aws" / "foundation" / "variables.tf").read_text(
        encoding="utf-8"
    )
    iam_hcl = (ROOT / "infra" / "aws" / "foundation" / "iam.tf").read_text(
        encoding="utf-8"
    )

    assert (
        '"token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"' in locals_hcl
    )
    assert (
        '"token.actions.githubusercontent.com:sub" = local.oidc_subject' in locals_hcl
    )
    assert "StringLike" not in locals_hcl
    assert 'default     = "kanfan/supportflow-ai"' in variables_hcl
    assert 'default     = "staging"' in variables_hcl
    assert 'variable "ecs_pass_role_arns"' in variables_hcl
    assert "default     = []" in variables_hcl
    assert "AdministratorAccess" not in iam_hcl
    assert "iam:PassRole" in iam_hcl
    assert 'values   = ["ecs-tasks.amazonaws.com"]' in iam_hcl


def test_terraform_provider_locks_cover_windows_and_linux() -> None:
    bootstrap_lock = (
        ROOT / "infra" / "aws" / "bootstrap" / ".terraform.lock.hcl"
    ).read_text(encoding="utf-8")
    foundation_lock = (
        ROOT / "infra" / "aws" / "foundation" / ".terraform.lock.hcl"
    ).read_text(encoding="utf-8")

    assert bootstrap_lock == foundation_lock
    assert bootstrap_lock.count('"h1:') >= 2
