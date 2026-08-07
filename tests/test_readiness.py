from collections.abc import Iterator
import logging
import os
from typing import cast

from fastapi.testclient import TestClient
import pytest
from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from app.config import Settings
from app.infrastructure.database import build_engine
from app.infrastructure.readiness import (
    AlwaysReadyProbe,
    ReadinessResult,
    RedisReadinessClient,
    RuntimeReadinessProbe,
)
from app.infrastructure.redis import build_redis_client
from app.main import create_app


class StubRedisReadinessClient:
    def __init__(self, result: bool = True) -> None:
        self._result = result

    def ping(self) -> object:
        return self._result


class FailingRedisReadinessClient:
    def ping(self) -> object:
        raise RedisConnectionError(
            "redis://user:super-secret@private-redis.internal:6379"
        )


class ClosingRedisClient:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FailingEngine:
    def connect(self) -> object:
        raise OperationalError(
            "postgresql://user:super-secret@private-db.internal/supportflow",
            {},
            RuntimeError("private database details"),
        )


def test_runtime_readiness_checks_postgresql_and_redis() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    probe = RuntimeReadinessProbe(engine, StubRedisReadinessClient())

    assert probe.check() == ReadinessResult()

    engine.dispose()


def test_runtime_readiness_reports_stable_dependency_categories() -> None:
    database_down = RuntimeReadinessProbe(
        cast(Engine, FailingEngine()),
        StubRedisReadinessClient(),
    )
    redis_down = RuntimeReadinessProbe(
        create_engine("sqlite+pysqlite:///:memory:"),
        FailingRedisReadinessClient(),
    )
    both_down = RuntimeReadinessProbe(
        cast(Engine, FailingEngine()),
        FailingRedisReadinessClient(),
    )

    assert database_down.check() == ReadinessResult(("postgresql",))
    assert redis_down.check() == ReadinessResult(("redis",))
    assert both_down.check() == ReadinessResult(("postgresql", "redis"))


def test_readiness_logs_exclude_dependency_exception_details(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class BothDownProbe:
        def check(self) -> ReadinessResult:
            return ReadinessResult(("postgresql", "redis"))

    caplog.set_level(logging.WARNING, logger="app.api.health")
    application = create_app(
        Settings(environment="test"),
        readiness_probe=BothDownProbe(),
    )

    with TestClient(application) as client:
        response = client.get("/health/ready")

    records = [
        record
        for record in caplog.records
        if record.message.startswith("readiness_dependency_unavailable")
    ]
    assert {getattr(record, "dependency") for record in records} == {
        "postgresql",
        "redis",
    }
    assert {getattr(record, "error_category") for record in records} == {"unavailable"}
    assert {getattr(record, "request_id") for record in records} == {
        response.headers["X-Request-ID"]
    }
    assert "super-secret" not in caplog.text
    assert "private-db" not in caplog.text
    assert "private-redis" not in caplog.text
    assert f"request_id={response.headers['X-Request-ID']}" in caplog.text
    assert "dependency=postgresql" in caplog.text
    assert "dependency=redis" in caplog.text


def test_application_closes_its_shared_redis_client() -> None:
    redis_client = ClosingRedisClient()
    application = create_app(
        Settings(environment="test"),
        redis_client=cast(Redis, redis_client),
        readiness_probe=AlwaysReadyProbe(),
    )

    with TestClient(application) as client:
        assert client.get("/health/ready").status_code == 200
        assert redis_client.closed is False

    assert redis_client.closed is True


@pytest.fixture
def real_readiness_dependencies() -> Iterator[tuple[Engine, Redis]]:
    database_url = os.getenv("SUPPORTFLOW_TEST_DATABASE_URL")
    redis_url = os.getenv("SUPPORTFLOW_TEST_REDIS_URL")
    if not database_url or not redis_url:
        pytest.skip("PostgreSQL and Redis test URLs are not configured")
    engine = build_engine(database_url, connect_timeout_seconds=2)
    redis_client = build_redis_client(redis_url, timeout_seconds=2)
    yield engine, redis_client
    engine.dispose()
    redis_client.close()


@pytest.mark.integration
def test_runtime_readiness_passes_against_real_postgresql_and_redis(
    real_readiness_dependencies: tuple[Engine, Redis],
) -> None:
    engine, redis_client = real_readiness_dependencies
    probe = RuntimeReadinessProbe(
        engine,
        cast(RedisReadinessClient, redis_client),
    )

    assert probe.check() == ReadinessResult()
