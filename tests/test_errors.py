from fastapi import HTTPException, Query
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


def test_validation_failure_does_not_echo_plaintext_password() -> None:
    application = create_app(Settings(environment="test"))

    with TestClient(application) as client:
        response = client.post(
            "/api/v1/auth/register",
            json={
                "email": "safe@example.com",
                "password": "short",
                "organization_name": "Safe Organization",
                "organization_slug": "safe-organization",
            },
        )

    assert response.status_code == 422
    assert '"short"' not in response.text


def test_http_exception_headers_are_preserved() -> None:
    application = create_app(Settings(environment="test"))

    @application.get("/authentication-required")
    async def authentication_required() -> None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    client = TestClient(application)
    response = client.get("/authentication-required")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
