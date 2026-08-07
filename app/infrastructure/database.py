from sqlalchemy import MetaData, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


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
) -> Engine:
    """Create a synchronous SQLAlchemy engine for API or worker processes."""

    connect_args = (
        {"connect_timeout": connect_timeout_seconds}
        if connect_timeout_seconds is not None
        else {}
    )
    return create_engine(
        database_url,
        echo=echo,
        pool_pre_ping=True,
        connect_args=connect_args,
    )


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create sessions whose loaded values remain usable after commit."""

    return sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )
