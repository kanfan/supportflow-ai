from collections.abc import Iterator
from datetime import timedelta
import os
from typing import cast
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from redis import Redis

from app.config import Settings
from app.infrastructure.readiness import AlwaysReadyProbe
from app.main import create_app
from app.ui.session import (
    RedisBrowserSessionStore,
    RedisSessionClient,
    UI_SESSION_COOKIE,
)


@pytest.fixture
def redis_session_client() -> Iterator[Redis]:
    redis_url = os.getenv("SUPPORTFLOW_TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("SUPPORTFLOW_TEST_REDIS_URL is not configured")
    client = Redis.from_url(
        redis_url,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    client.ping()
    yield client
    client.close()


def redis_client_from_environment() -> Redis:
    redis_url = os.getenv("SUPPORTFLOW_TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("SUPPORTFLOW_TEST_REDIS_URL is not configured")
    client = Redis.from_url(
        redis_url,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    client.ping()
    return client


@pytest.mark.integration
def test_two_store_instances_share_rotation_and_invalidation(
    redis_session_client: Redis,
) -> None:
    key_prefix = f"supportflow:test:ui-session:{uuid4()}:"
    secret = "ui-session-integration-secret-with-thirty-two-bytes"
    first_store = RedisBrowserSessionStore(
        client=cast(RedisSessionClient, redis_session_client),
        secret_key=secret,
        lifetime=timedelta(minutes=5),
        key_prefix=key_prefix,
    )
    second_store = RedisBrowserSessionStore(
        client=cast(RedisSessionClient, redis_session_client),
        secret_key=secret,
        lifetime=timedelta(minutes=5),
        key_prefix=key_prefix,
    )

    try:
        anonymous_cookie, anonymous = first_store.create_anonymous()
        assert second_store.resolve(anonymous_cookie) == anonymous
        anonymous_session_id = anonymous_cookie.split(".", 1)[0]
        ttl = cast(
            int,
            redis_session_client.ttl(f"{key_prefix}{anonymous_session_id}"),
        )
        assert 0 < ttl <= 300

        authenticated_cookie, authenticated = second_store.authenticate(
            previous_cookie=anonymous_cookie,
            user_id=uuid4(),
            organization_id=uuid4(),
        )
        assert first_store.resolve(anonymous_cookie) is None
        assert first_store.resolve(authenticated_cookie) == authenticated

        first_store.invalidate(authenticated_cookie)
        assert second_store.resolve(authenticated_cookie) is None
    finally:
        keys = list(redis_session_client.scan_iter(match=f"{key_prefix}*"))
        if keys:
            redis_session_client.delete(*keys)


@pytest.mark.integration
def test_browser_session_survives_api_application_replacement() -> None:
    first_redis_client = redis_client_from_environment()
    first_application = create_app(
        Settings(environment="test"),
        redis_client=first_redis_client,
        readiness_probe=AlwaysReadyProbe(),
    )

    with TestClient(first_application) as first_api:
        response = first_api.get("/ui/login")
        assert response.status_code == 200
        cookie_value = first_api.cookies.get(UI_SESSION_COOKIE)
        assert cookie_value is not None

    second_redis_client = redis_client_from_environment()
    second_application = create_app(
        Settings(environment="test"),
        redis_client=second_redis_client,
        readiness_probe=AlwaysReadyProbe(),
    )
    with TestClient(second_application):
        replacement_store = cast(
            RedisBrowserSessionStore,
            second_application.state.browser_session_store,
        )
        try:
            restored_session = replacement_store.resolve(cookie_value)
            assert restored_session is not None
            assert restored_session.is_authenticated is False
        finally:
            replacement_store.invalidate(cookie_value)
