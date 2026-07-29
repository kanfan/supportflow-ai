import pytest

from scripts.week3_performance_baseline import validate_performance_database_url


@pytest.mark.parametrize(
    "database_url",
    (
        "postgresql+psycopg://user:password@localhost:5432/supportflow_perf",
        "postgresql+psycopg://user:password@127.0.0.1:5432/supportflow_perf",
        "postgresql+psycopg://user:password@[::1]:5432/supportflow_perf",
    ),
)
def test_performance_reset_accepts_only_named_loopback_database(
    database_url: str,
) -> None:
    validate_performance_database_url(database_url)


@pytest.mark.parametrize(
    ("database_url", "message"),
    (
        (
            "postgresql+psycopg://user:password@localhost:5432/production",
            "database named supportflow_perf",
        ),
        (
            "postgresql+psycopg://user:password@db.example.com:5432/supportflow_perf",
            "localhost or a loopback IP",
        ),
    ),
)
def test_performance_reset_rejects_wrong_database_or_remote_host(
    database_url: str,
    message: str,
) -> None:
    with pytest.raises(RuntimeError, match=message):
        validate_performance_database_url(database_url)
