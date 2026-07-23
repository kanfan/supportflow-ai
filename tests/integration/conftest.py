from collections.abc import Iterator
import os
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session

from app.infrastructure.database import build_engine


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def migrated_database_url() -> Iterator[str]:
    database_url = os.getenv("SUPPORTFLOW_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("SUPPORTFLOW_TEST_DATABASE_URL is not configured")

    database_name = make_url(database_url).database
    if database_name != "supportflow_test":
        pytest.fail(
            "Integration tests require a disposable database named supportflow_test"
        )

    alembic_config = Config(PROJECT_ROOT / "alembic.ini")
    alembic_config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    alembic_config.set_main_option(
        "sqlalchemy.url",
        database_url.replace("%", "%%"),
    )

    command.upgrade(alembic_config, "head")
    command.check(alembic_config)
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")

    yield database_url

    command.downgrade(alembic_config, "base")


@pytest.fixture(scope="session")
def database_engine(migrated_database_url: str) -> Iterator[Engine]:
    engine = build_engine(migrated_database_url)
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_identity_tables(database_engine: Engine) -> Iterator[None]:
    def truncate() -> None:
        with database_engine.begin() as connection:
            connection.execute(
                text(
                    "TRUNCATE TABLE organization_members, users, organizations CASCADE"
                )
            )

    truncate()
    yield
    truncate()


@pytest.fixture
def database_session(database_engine: Engine) -> Iterator[Session]:
    connection = database_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, expire_on_commit=False)

    yield session

    session.close()
    transaction.rollback()
    connection.close()
