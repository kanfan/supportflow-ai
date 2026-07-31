from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI

from app.api.audit_events import router as audit_events_router
from app.api.auth import router as auth_router
from app.api.documents import router as documents_router
from app.api.health import router as health_router
from app.api.organization_members import router as organization_members_router
from app.api.tickets import router as tickets_router
from app.auth.security import AccessTokenManager, PasswordManager
from app.config import Settings, get_settings
from app.documents.composition import resolve_document_safety_scanner
from app.documents.dispatch import CeleryDocumentTaskDispatcher
from app.documents.ports import (
    DocumentSafetyScanner,
    DocumentStorage,
    DocumentTaskDispatcher,
)
from app.documents.storage import LocalDocumentStorage
from app.errors import install_error_handlers
from app.infrastructure.database import build_engine, build_session_factory
from app.ui.router import router as ui_router
from app.ui.session import BrowserSessionStore
from app.worker import create_celery


def create_app(
    settings: Settings | None = None,
    *,
    document_safety_scanner: DocumentSafetyScanner | None = None,
    document_storage: DocumentStorage | None = None,
    document_task_dispatcher: DocumentTaskDispatcher | None = None,
) -> FastAPI:
    resolved_settings = settings or get_settings()
    resolved_document_safety_scanner = resolve_document_safety_scanner(
        resolved_settings,
        document_safety_scanner,
    )
    engine = build_engine(resolved_settings.database_url)
    session_factory = build_session_factory(engine)

    @asynccontextmanager
    async def lifespan(_application: FastAPI) -> AsyncIterator[None]:
        yield
        engine.dispose()

    application = FastAPI(
        title=resolved_settings.app_name,
        version=resolved_settings.app_version,
        debug=resolved_settings.debug,
        lifespan=lifespan,
    )
    application.state.settings = resolved_settings
    application.state.session_factory = session_factory
    application.state.document_safety_scanner = resolved_document_safety_scanner
    application.state.document_storage = (
        document_storage
        if document_storage is not None
        else LocalDocumentStorage(resolved_settings.document_storage_root)
    )
    application.state.document_task_dispatcher = (
        document_task_dispatcher
        if document_task_dispatcher is not None
        else CeleryDocumentTaskDispatcher(create_celery(resolved_settings))
    )
    application.state.document_max_upload_bytes = (
        resolved_settings.document_max_upload_bytes
    )
    application.state.password_manager = PasswordManager()
    application.state.access_token_manager = AccessTokenManager.from_settings(
        resolved_settings
    )
    application.state.browser_session_store = BrowserSessionStore(
        secret_key=resolved_settings.auth_secret_key.get_secret_value(),
        lifetime=timedelta(minutes=resolved_settings.ui_session_ttl_minutes),
    )
    install_error_handlers(application)
    application.include_router(health_router)
    application.include_router(auth_router)
    application.include_router(documents_router)
    application.include_router(audit_events_router)
    application.include_router(organization_members_router)
    application.include_router(tickets_router)
    application.include_router(ui_router)
    return application


app = create_app()
