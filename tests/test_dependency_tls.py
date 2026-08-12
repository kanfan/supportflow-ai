import ssl

from redis.connection import Connection, SSLConnection

from app.infrastructure.redis import build_redis_client


def test_rediss_client_requires_certificate_and_hostname_verification() -> None:
    client = build_redis_client(
        "rediss://default:secret@cache.internal:6379/0",
        timeout_seconds=2,
    )

    try:
        assert client.connection_pool.connection_class is SSLConnection
        options = client.connection_pool.connection_kwargs
        assert options["ssl_cert_reqs"] == ssl.CERT_REQUIRED
        assert options["ssl_check_hostname"] is True
        assert options["socket_connect_timeout"] == 2
        assert options["socket_timeout"] == 2
    finally:
        client.close()


def test_local_plain_redis_client_remains_available_for_compose() -> None:
    client = build_redis_client("redis://localhost:6379/0", timeout_seconds=2)

    try:
        assert client.connection_pool.connection_class is Connection
        assert "ssl_cert_reqs" not in client.connection_pool.connection_kwargs
    finally:
        client.close()
