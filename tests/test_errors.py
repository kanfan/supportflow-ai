from fastapi import Query
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_not_found_uses_error_envelope() -> None:
    client = TestClient(create_app(Settings(environment="test")))

    response = client.get("/does-not-exist")

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "not_found",
            "message": "Not Found",
            "details": None,
        }
    }


def test_validation_failure_uses_error_envelope() -> None:
    application = create_app(Settings(environment="test"))

    @application.get("/validated")
    async def validated(limit: int = Query(ge=1)) -> dict[str, int]:
        return {"limit": limit}

    client = TestClient(application)
    response = client.get("/validated", params={"limit": 0})

    assert response.status_code == 422
    payload = response.json()
    assert payload["error"]["code"] == "validation_error"
    assert payload["error"]["message"] == "Request validation failed"
    assert payload["error"]["details"]
