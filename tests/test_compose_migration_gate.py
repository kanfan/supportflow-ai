from pathlib import Path
import yaml


def test_all_application_processes_wait_for_migration_without_cycle():
    services = yaml.safe_load(Path("compose.yaml").read_text())["services"]
    for name in ("api", "worker", "relay"):
        assert (
            services[name]["depends_on"]["migrate"]["condition"]
            == "service_completed_successfully"
        )
    assert services["migrate"]["depends_on"] == {
        "postgres": {"condition": "service_healthy"}
    }
    assert "depends_on" not in services["postgres"]


def test_failure_override_is_only_a_failed_migration():
    override = yaml.safe_load(Path("compose.migration-failure.yaml").read_text())
    assert override == {
        "services": {"migrate": {"command": ["python", "-c", "raise SystemExit(17)"]}}
    }
