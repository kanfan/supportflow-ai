from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.ui.session import BrowserSessionStore


def test_browser_session_login_rotates_cookie_and_csrf_and_invalidates_old_state() -> (
    None
):
    now = datetime(2026, 7, 26, tzinfo=UTC)
    store = BrowserSessionStore(
        secret_key="ui-session-test-secret-with-thirty-two-bytes",
        lifetime=timedelta(hours=8),
        clock=lambda: now,
    )
    anonymous_cookie, anonymous = store.create_anonymous()

    authenticated_cookie, authenticated = store.authenticate(
        previous_cookie=anonymous_cookie,
        user_id=uuid4(),
        organization_id=uuid4(),
    )

    assert store.resolve(anonymous_cookie) is None
    assert authenticated_cookie != anonymous_cookie
    assert authenticated.csrf_token != anonymous.csrf_token
    assert authenticated.is_authenticated is True
    assert store.resolve(authenticated_cookie) == authenticated


def test_browser_session_rejects_tampering_expiry_and_logout_replay() -> None:
    current_time = datetime(2026, 7, 26, tzinfo=UTC)
    store = BrowserSessionStore(
        secret_key="ui-session-test-secret-with-thirty-two-bytes",
        lifetime=timedelta(minutes=30),
        clock=lambda: current_time,
    )
    cookie, session = store.create_anonymous()

    assert store.resolve(f"{cookie}tampered") is None
    assert store.resolve(cookie) == session

    store.invalidate(cookie)
    assert store.resolve(cookie) is None

    expiring_cookie, _ = store.create_anonymous()
    current_time = current_time + timedelta(minutes=31)
    assert store.resolve(expiring_cookie) is None
