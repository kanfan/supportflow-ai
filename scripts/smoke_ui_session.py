from datetime import timedelta
from http.cookies import SimpleCookie
import os
from typing import cast
from urllib.request import urlopen

from app.config import get_settings
from app.infrastructure.redis import build_redis_client
from app.ui.session import (
    RedisBrowserSessionStore,
    RedisSessionClient,
    UI_SESSION_COOKIE,
)


def main() -> None:
    settings = get_settings()
    api_base_url = os.getenv("SUPPORTFLOW_SMOKE_API_URL", "http://api:8000")
    with urlopen(f"{api_base_url}/ui/login", timeout=10) as response:  # noqa: S310
        set_cookie = response.headers.get("Set-Cookie")
        response.read()
    if set_cookie is None:
        raise RuntimeError("UI session smoke did not receive a session cookie")

    parsed_cookie = SimpleCookie()
    parsed_cookie.load(set_cookie)
    morsel = parsed_cookie.get(UI_SESSION_COOKIE)
    if morsel is None:
        raise RuntimeError("UI session smoke received an unexpected cookie")

    redis_client = build_redis_client(
        settings.redis_url.get_secret_value(),
        timeout_seconds=settings.dependency_connect_timeout_seconds,
    )
    independent_store = RedisBrowserSessionStore(
        client=cast(RedisSessionClient, redis_client),
        secret_key=settings.auth_secret_key.get_secret_value(),
        lifetime=timedelta(minutes=settings.ui_session_ttl_minutes),
    )
    try:
        session = independent_store.resolve(morsel.value)
        if session is None or session.is_authenticated:
            raise RuntimeError("UI session is not visible through shared Redis storage")
        independent_store.invalidate(morsel.value)
        if independent_store.resolve(morsel.value) is not None:
            raise RuntimeError("Invalidated UI session can still be replayed")
    finally:
        redis_client.close()

    print("Shared UI session smoke test passed")


if __name__ == "__main__":
    main()
