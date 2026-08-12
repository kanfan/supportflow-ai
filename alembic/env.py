from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from app.audit import models as audit_models  # noqa: F401
from app.config import get_settings
from app.documents import models as document_models  # noqa: F401
from app.identity import models as identity_models  # noqa: F401
from app.infrastructure.database import Base, build_engine
from app.tickets import models as ticket_models  # noqa: F401


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def resolve_database_url() -> str:
    configured_url = config.get_main_option("sqlalchemy.url")
    if configured_url:
        return configured_url
    return get_settings().database_url.get_secret_value()


def resolve_database_ssl_root_cert_path() -> str | None:
    configured_path = get_settings().database_ssl_root_cert_path
    return str(configured_path) if configured_path is not None else None


def run_migrations_offline() -> None:
    context.configure(
        url=resolve_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = build_engine(
        resolve_database_url(),
        ssl_root_cert_path=resolve_database_ssl_root_cert_path(),
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
