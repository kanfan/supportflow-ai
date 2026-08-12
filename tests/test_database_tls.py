from pathlib import Path
from typing import cast

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

from app.infrastructure.database import build_engine


def test_deployed_engine_forces_full_verification_and_explicit_ca(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    expected_engine = cast(Engine, object())

    def record_create_engine(database_url: str, **options: object) -> Engine:
        captured["database_url"] = database_url
        captured.update(options)
        return expected_engine

    monkeypatch.setattr(
        "app.infrastructure.database.create_engine",
        record_create_engine,
    )
    ca_path = Path("/app/certs/rds-ca-bundle.pem")

    engine = build_engine(
        "postgresql+psycopg://supportflow:secret@db.internal/supportflow",
        connect_timeout_seconds=2,
        ssl_root_cert_path=ca_path,
        poolclass=NullPool,
    )

    assert engine is expected_engine
    assert captured["connect_args"] == {
        "connect_timeout": 2,
        "sslmode": "verify-full",
        "sslrootcert": str(ca_path),
    }
    assert captured["pool_timeout"] == 2
    assert captured["poolclass"] is NullPool
    assert "secret" in cast(str, captured["database_url"])


def test_local_engine_does_not_add_tls_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def record_create_engine(_database_url: str, **options: object) -> Engine:
        captured.update(options)
        return cast(Engine, object())

    monkeypatch.setattr(
        "app.infrastructure.database.create_engine",
        record_create_engine,
    )

    build_engine(
        "postgresql+psycopg://supportflow:supportflow@localhost/supportflow",
        connect_timeout_seconds=2,
    )

    assert captured["connect_args"] == {"connect_timeout": 2}
