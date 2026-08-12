from pathlib import Path
from typing import Any

from sqlalchemy import MetaData, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import Pool


NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base shared by every persisted model."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def build_engine(
    database_url: str,
    *,
    echo: bool = False,
    connect_timeout_seconds: int | None = None,
    ssl_root_cert_path: str | Path | None = None,
    poolclass: type[Pool] | None = None,
) -> Engine:
    """Create a synchronous SQLAlchemy engine for API or worker processes."""

    connect_args: dict[str, object] = {}
    if connect_timeout_seconds is not None:
        connect_args["connect_timeout"] = connect_timeout_seconds
    if ssl_root_cert_path is not None:
        connect_args.update(
            sslmode="verify-full",
            sslrootcert=str(ssl_root_cert_path),
        )

    engine_options: dict[str, Any] = {
        "echo": echo,
        "pool_pre_ping": True,
    }
    if connect_args:
        engine_options["connect_args"] = connect_args
    if connect_timeout_seconds is not None:
        engine_options["pool_timeout"] = connect_timeout_seconds
    if poolclass is not None:
        engine_options["poolclass"] = poolclass

    return create_engine(
        database_url,
        **engine_options,
    )


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create sessions whose loaded values remain usable after commit."""

    return sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )
