from uuid import UUID

from fastapi.testclient import TestClient

from app.config import Settings
from app.infrastructure.readiness import ReadinessResult
from app.main import create_app
from app.observability import REQUEST_ID_HEADER


class StubReadinessProbe:
    def __init__(self, result: ReadinessResult) -> None:
        self._result = result
        self.calls = 0

    def check(self) -> ReadinessResult:
        self.calls += 1
        return self._result


def test_live_health_returns_ok_without_checking_dependencies() -> None:
    probe = StubReadinessProbe(ReadinessResult(("postgresql", "redis")))
    client = TestClient(create_app(Settings(environment="test"), readiness_probe=probe))

    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert probe.calls == 0


def test_ready_health_returns_ok_when_dependencies_are_available() -> None:
    probe = StubReadinessProbe(ReadinessResult())
    client = TestClient(create_app(Settings(environment="test"), readiness_probe=probe))

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert probe.calls == 1


def test_ready_health_returns_generic_503() -> None:
    probe = StubReadinessProbe(ReadinessResult(("postgresql", "redis")))
    client = TestClient(create_app(Settings(environment="test"), readiness_probe=probe))
    with client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert "postgresql" not in response.text
    assert "redis" not in response.text


def test_request_ids_are_server_generated_bounded_and_unique() -> None:
    client = TestClient(create_app(Settings(environment="test")))

    first = client.get(
        "/health/live",
        headers={REQUEST_ID_HEADER: "attacker-controlled-value"},
    )
    second = client.get("/health/live")

    first_request_id = first.headers[REQUEST_ID_HEADER]
    second_request_id = second.headers[REQUEST_ID_HEADER]
    assert UUID(first_request_id).version == 4
    assert UUID(second_request_id).version == 4
    assert first_request_id != "attacker-controlled-value"
    assert first_request_id != second_request_id


def test_app_factory_uses_supplied_settings() -> None:
    application = create_app(
        Settings(
            app_name="SupportFlow Test",
            app_version="9.9.9",
            environment="test",
        )
    )

    assert application.title == "SupportFlow Test"
    assert application.version == "9.9.9"
