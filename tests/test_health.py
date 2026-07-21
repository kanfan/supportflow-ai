from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


client = TestClient(create_app(Settings(environment="test")))


def test_live_health_returns_ok() -> None:
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


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
