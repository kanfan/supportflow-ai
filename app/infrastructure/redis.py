import ssl
from urllib.parse import urlsplit

from redis import Redis


def build_redis_client(redis_url: str, *, timeout_seconds: int) -> Redis:
    """Build the shared synchronous Redis client owned by the API process."""

    common_options = {
        "decode_responses": True,
        "socket_connect_timeout": timeout_seconds,
        "socket_timeout": timeout_seconds,
    }
    if urlsplit(redis_url).scheme.lower() == "rediss":
        return Redis.from_url(
            redis_url,
            ssl_cert_reqs=ssl.CERT_REQUIRED,
            ssl_check_hostname=True,
            **common_options,
        )
    return Redis.from_url(redis_url, **common_options)
