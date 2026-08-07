from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError

from app.config import Settings
from app.main import create_app
from app.ui.session import (
    BrowserSessionStoreUnavailableError,
    InMemoryBrowserSessionStore,
    RedisBrowserSessionStore,
)


@dataclass(frozen=True)
class DeleteOperation:
    names: tuple[str, ...]


@dataclass(frozen=True)
class SetOperation:
    name: str
    value: str
    expires_in_seconds: int


type FakeRedisOperation = DeleteOperation | SetOperation


class FakeRedisPipeline:
    def __init__(self, client: "FakeRedisClient") -> None:
        self._client = client
        self._operations: list[FakeRedisOperation] = []

    def delete(self, *names: str) -> "FakeRedisPipeline":
        self._operations.append(DeleteOperation(names))
        return self

    def set(
        self,
        name: str,
        value: str,
        *,
        ex: int,
    ) -> "FakeRedisPipeline":
        self._operations.append(SetOperation(name, value, ex))
        return self

    def execute(self) -> list[object]:
        results: list[object] = []
        for operation in self._operations:
            if isinstance(operation, DeleteOperation):
                results.append(self._client.delete(*operation.names))
            else:
                results.append(
                    self._client.set(
                        operation.name,
                        operation.value,
                        ex=operation.expires_in_seconds,
                    )
                )
        return results


class FakeRedisClient:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def delete(self, *names: str) -> int:
        deleted = 0
        for name in names:
            if name in self.values:
                deleted += 1
            self.values.pop(name, None)
            self.ttls.pop(name, None)
        return deleted

    def set(self, name: str, value: str, *, ex: int) -> bool:
        self.values[name] = value
        self.ttls[name] = ex
        return True

    def pipeline(self, *, transaction: bool) -> FakeRedisPipeline:
        assert transaction is True
        return FakeRedisPipeline(self)


class FailingRedisClient(FakeRedisClient):
    def get(self, name: str) -> str | None:
        del name
        raise RedisConnectionError("redis://user:super-secret@private-host:6379")

    def delete(self, *names: str) -> int:
        del names
        raise RedisConnectionError("redis://user:super-secret@private-host:6379")

    def pipeline(self, *, transaction: bool) -> FakeRedisPipeline:
        del transaction
        raise RedisConnectionError("redis://user:super-secret@private-host:6379")


class FailingExecutePipeline(FakeRedisPipeline):
    def execute(self) -> list[object]:
        raise RedisConnectionError("redis transaction interrupted")


class FailingExecuteRedisClient(FakeRedisClient):
    def __init__(self) -> None:
        super().__init__()
        self.fail_execute = False

    def pipeline(self, *, transaction: bool) -> FakeRedisPipeline:
        assert transaction is True
        if self.fail_execute:
            return FailingExecutePipeline(self)
        return FakeRedisPipeline(self)


def test_browser_session_login_rotates_cookie_and_csrf_and_invalidates_old_state() -> (
    None
):
    now = datetime(2026, 7, 26, tzinfo=UTC)
    store = InMemoryBrowserSessionStore(
        secret_key="ui-session-test-secret-with-thirty-two-bytes",
        lifetime=timedelta(hours=8),
        clock=lambda: now,
    )
    anonymous_cookie, anonymous = store.create_anonymous()

    authenticated_cookie, authenticated = store.authenticate(
        previous_cookie=anonymous_cookie,
        user_id=uuid4(),
        organization_id=uuid4(),
    )

    assert store.resolve(anonymous_cookie) is None
    assert authenticated_cookie != anonymous_cookie
    assert authenticated.csrf_token != anonymous.csrf_token
    assert authenticated.is_authenticated is True
    assert store.resolve(authenticated_cookie) == authenticated


def test_browser_session_rejects_tampering_expiry_and_logout_replay() -> None:
    current_time = datetime(2026, 7, 26, tzinfo=UTC)
    store = InMemoryBrowserSessionStore(
        secret_key="ui-session-test-secret-with-thirty-two-bytes",
        lifetime=timedelta(minutes=30),
        clock=lambda: current_time,
    )
    cookie, session = store.create_anonymous()

    assert store.resolve(f"{cookie}tampered") is None
    assert store.resolve(cookie) == session

    store.invalidate(cookie)
    assert store.resolve(cookie) is None

    expiring_cookie, _ = store.create_anonymous()
    current_time = current_time + timedelta(minutes=31)
    assert store.resolve(expiring_cookie) is None


def test_redis_session_is_shared_and_rotation_is_atomic() -> None:
    client = FakeRedisClient()
    secret = "ui-session-test-secret-with-thirty-two-bytes"
    lifetime = timedelta(minutes=30)
    first_store = RedisBrowserSessionStore(
        client=client,
        secret_key=secret,
        lifetime=lifetime,
    )
    second_store = RedisBrowserSessionStore(
        client=client,
        secret_key=secret,
        lifetime=lifetime,
    )

    anonymous_cookie, anonymous = first_store.create_anonymous()
    assert second_store.resolve(anonymous_cookie) == anonymous

    authenticated_cookie, authenticated = second_store.authenticate(
        previous_cookie=anonymous_cookie,
        user_id=uuid4(),
        organization_id=uuid4(),
    )

    assert first_store.resolve(anonymous_cookie) is None
    assert first_store.resolve(authenticated_cookie) == authenticated
    assert authenticated_cookie != anonymous_cookie
    assert authenticated.csrf_token != anonymous.csrf_token
    assert set(client.ttls.values()) == {1800}


def test_redis_session_rejects_tampering_expiry_logout_replay_and_bad_data() -> None:
    current_time = datetime(2026, 8, 7, tzinfo=UTC)
    client = FakeRedisClient()
    store = RedisBrowserSessionStore(
        client=client,
        secret_key="ui-session-test-secret-with-thirty-two-bytes",
        lifetime=timedelta(minutes=30),
        clock=lambda: current_time,
    )
    cookie, session = store.create_anonymous()

    assert store.resolve(f"{cookie}tampered") is None
    assert store.resolve(cookie) == session

    store.invalidate(cookie)
    assert store.resolve(cookie) is None

    expired_cookie, _ = store.create_anonymous()
    current_time = current_time + timedelta(minutes=31)
    assert store.resolve(expired_cookie) is None

    corrupt_cookie, _ = store.create_anonymous()
    corrupt_session_id = corrupt_cookie.split(".", 1)[0]
    corrupt_key = f"supportflow:ui-session:v1:{corrupt_session_id}"
    client.values[corrupt_key] = '{"csrf_token":"unexpected-fields-are-rejected"}'
    assert store.resolve(corrupt_cookie) is None
    assert corrupt_key not in client.values


def test_failed_redis_rotation_does_not_delete_the_previous_session() -> None:
    client = FailingExecuteRedisClient()
    store = RedisBrowserSessionStore(
        client=client,
        secret_key="ui-session-test-secret-with-thirty-two-bytes",
        lifetime=timedelta(minutes=30),
    )
    anonymous_cookie, anonymous = store.create_anonymous()
    client.fail_execute = True

    with pytest.raises(BrowserSessionStoreUnavailableError):
        store.authenticate(
            previous_cookie=anonymous_cookie,
            user_id=uuid4(),
            organization_id=uuid4(),
        )

    assert store.resolve(anonymous_cookie) == anonymous


def test_redis_session_failure_uses_stable_error_without_connection_details() -> None:
    store = RedisBrowserSessionStore(
        client=FailingRedisClient(),
        secret_key="ui-session-test-secret-with-thirty-two-bytes",
        lifetime=timedelta(minutes=30),
    )

    with pytest.raises(BrowserSessionStoreUnavailableError) as create_error:
        store.create_anonymous()
    assert str(create_error.value) == "Browser session storage is unavailable"
    assert "super-secret" not in str(create_error.value)

    valid_client = FakeRedisClient()
    valid_store = RedisBrowserSessionStore(
        client=valid_client,
        secret_key="ui-session-test-secret-with-thirty-two-bytes",
        lifetime=timedelta(minutes=30),
    )
    cookie, _ = valid_store.create_anonymous()
    failing_store = RedisBrowserSessionStore(
        client=FailingRedisClient(),
        secret_key="ui-session-test-secret-with-thirty-two-bytes",
        lifetime=timedelta(minutes=30),
    )
    with pytest.raises(BrowserSessionStoreUnavailableError) as resolve_error:
        failing_store.resolve(cookie)
    assert "private-host" not in str(resolve_error.value)


def test_ui_returns_safe_service_unavailable_when_redis_is_down() -> None:
    failing_store = RedisBrowserSessionStore(
        client=FailingRedisClient(),
        secret_key="ui-session-test-secret-with-thirty-two-bytes",
        lifetime=timedelta(minutes=30),
    )
    application = create_app(
        Settings(environment="test"),
        browser_session_store=failing_store,
    )

    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.get("/ui/login")

    assert response.status_code == 503
    assert "Oturum hizmeti geçici olarak kullanılamıyor" in response.text
    assert "super-secret" not in response.text
    assert "private-host" not in response.text


def test_application_composition_uses_shared_sessions_outside_tests() -> None:
    local_application = create_app(Settings(environment="local"))
    test_application = create_app(Settings(environment="test"))

    assert isinstance(
        local_application.state.browser_session_store,
        RedisBrowserSessionStore,
    )
    assert isinstance(
        test_application.state.browser_session_store,
        InMemoryBrowserSessionStore,
    )
