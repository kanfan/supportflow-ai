from redis import Redis


def build_redis_client(redis_url: str, *, timeout_seconds: int) -> Redis:
    """Build the shared synchronous Redis client owned by the API process."""

    return Redis.from_url(
        redis_url,
        decode_responses=True,
        socket_connect_timeout=timeout_seconds,
        socket_timeout=timeout_seconds,
    )
