from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import cast

from fastapi import FastAPI
from redis import Redis

from app.api.audit_events import router as audit_events_router
from app.api.auth import router as auth_router
from app.api.documents import router as documents_router
from app.api.health import router as health_router
from app.api.organization_members import router as organization_members_router
from app.api.tickets import router as tickets_router
from app.auth.security import AccessTokenManager, PasswordManager
from app.config import Settings, get_settings
from app.documents.composition import (
    close_document_storage,
    resolve_document_safety_scanner,
    resolve_document_storage,
)
from app.documents.dispatch import CeleryDocumentTaskDispatcher
from app.documents.ports import (
    DocumentSafetyScanner,
    DocumentStorage,
    DocumentTaskDispatcher,
)
from app.errors import install_error_handlers
from app.infrastructure.database import build_engine, build_session_factory
from app.infrastructure.readiness import (
    AlwaysReadyProbe,
    ReadinessProbe,
    RedisReadinessClient,
    RuntimeReadinessProbe,
)
from app.infrastructure.redis import build_redis_client
from app.observability import RequestIdMiddleware
from app.ui.router import browser_session_unavailable_handler, router as ui_router
from app.ui.session import (
    BrowserSessionStore,
    BrowserSessionStoreUnavailableError,
    InMemoryBrowserSessionStore,
    RedisBrowserSessionStore,
    RedisSessionClient,
)
from app.worker import create_celery_client


def create_app(
    settings: Settings | None = None,
    *,
    document_safety_scanner: DocumentSafetyScanner | None = None,
    document_storage: DocumentStorage | None = None,
    document_task_dispatcher: DocumentTaskDispatcher | None = None,
    browser_session_store: BrowserSessionStore | None = None,
    readiness_probe: ReadinessProbe | None = None,
    redis_client: Redis | None = None,
) -> FastAPI:
    resolved_settings = settings or get_settings()
    resolved_document_safety_scanner = resolve_document_safety_scanner(
        resolved_settings,
        document_safety_scanner,
    )
    resolved_document_storage = resolve_document_storage(
        resolved_settings,
        document_storage,
    )
    engine = build_engine(
        resolved_settings.database_url,
        connect_timeout_seconds=(resolved_settings.dependency_connect_timeout_seconds),
    )
    session_factory = build_session_factory(engine)
    resolved_redis_client = redis_client
    if resolved_redis_client is None and resolved_settings.environment != "test":
        resolved_redis_client = build_redis_client(
            resolved_settings.redis_url,
            timeout_seconds=resolved_settings.dependency_connect_timeout_seconds,
        )

    @asynccontextmanager
    async def lifespan(_application: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            engine.dispose()
            close_document_storage(resolved_document_storage)
            if resolved_redis_client is not None:
                resolved_redis_client.close()

    application = FastAPI(
        title=resolved_settings.app_name,
        version=resolved_settings.app_version,
        debug=resolved_settings.debug,
        lifespan=lifespan,
    )
    application.add_middleware(RequestIdMiddleware)
    application.state.settings = resolved_settings
    application.state.session_factory = session_factory
    application.state.document_safety_scanner = resolved_document_safety_scanner
    application.state.document_storage = resolved_document_storage
    application.state.document_task_dispatcher = (
        document_task_dispatcher
        if document_task_dispatcher is not None
        else CeleryDocumentTaskDispatcher(create_celery_client(resolved_settings))
    )
    application.state.document_max_upload_bytes = (
        resolved_settings.document_max_upload_bytes
    )
    application.state.password_manager = PasswordManager()
    application.state.access_token_manager = AccessTokenManager.from_settings(
        resolved_settings
    )
    session_store_lifetime = timedelta(minutes=resolved_settings.ui_session_ttl_minutes)
    if browser_session_store is not None:
        resolved_browser_session_store = browser_session_store
    elif resolved_redis_client is not None:
        resolved_browser_session_store = RedisBrowserSessionStore(
            client=cast(RedisSessionClient, resolved_redis_client),
            secret_key=resolved_settings.auth_secret_key.get_secret_value(),
            lifetime=session_store_lifetime,
        )
    else:
        resolved_browser_session_store = InMemoryBrowserSessionStore(
            secret_key=resolved_settings.auth_secret_key.get_secret_value(),
            lifetime=session_store_lifetime,
        )
    application.state.browser_session_store = resolved_browser_session_store
    if readiness_probe is not None:
        resolved_readiness_probe = readiness_probe
    elif resolved_redis_client is not None:
        resolved_readiness_probe = RuntimeReadinessProbe(
            engine,
            cast(RedisReadinessClient, resolved_redis_client),
        )
    else:
        resolved_readiness_probe = AlwaysReadyProbe()
    application.state.readiness_probe = resolved_readiness_probe
    install_error_handlers(application)
    application.add_exception_handler(
        BrowserSessionStoreUnavailableError,
        browser_session_unavailable_handler,
    )
    application.include_router(health_router)
    application.include_router(auth_router)
    application.include_router(documents_router)
    application.include_router(audit_events_router)
    application.include_router(organization_members_router)
    application.include_router(tickets_router)
    application.include_router(ui_router)
    return application


app = create_app()
