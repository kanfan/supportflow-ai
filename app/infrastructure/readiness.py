from dataclasses import dataclass
from typing import Literal, Protocol

from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError


DependencyName = Literal["postgresql", "redis"]


@dataclass(frozen=True)
class ReadinessResult:
    unavailable_dependencies: tuple[DependencyName, ...] = ()

    @property
    def is_ready(self) -> bool:
        return not self.unavailable_dependencies


class ReadinessProbe(Protocol):
    def check(self) -> ReadinessResult: ...


class RedisReadinessClient(Protocol):
    def ping(self) -> object: ...


class RuntimeReadinessProbe:
    """Checks the runtime PostgreSQL and Redis dependencies without leaking errors."""

    def __init__(self, engine: Engine, redis_client: RedisReadinessClient) -> None:
        self._engine = engine
        self._redis_client = redis_client

    def check(self) -> ReadinessResult:
        unavailable: list[DependencyName] = []
        try:
            with self._engine.connect() as connection:
                if connection.execute(text("SELECT 1")).scalar_one() != 1:
                    unavailable.append("postgresql")
        except SQLAlchemyError:
            unavailable.append("postgresql")

        try:
            if self._redis_client.ping() is not True:
                unavailable.append("redis")
        except RedisError:
            unavailable.append("redis")

        return ReadinessResult(tuple(unavailable))


class AlwaysReadyProbe:
    """Deterministic default used only by isolated test application factories."""

    def check(self) -> ReadinessResult:
        return ReadinessResult()
