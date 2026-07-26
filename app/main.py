from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.audit_events import router as audit_events_router
from app.api.auth import router as auth_router
from app.api.health import router as health_router
from app.api.organization_members import router as organization_members_router
from app.api.tickets import router as tickets_router
from app.auth.security import AccessTokenManager, PasswordManager
from app.config import Settings, get_settings
from app.errors import install_error_handlers
from app.infrastructure.database import build_engine, build_session_factory


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
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
    application.state.password_manager = PasswordManager()
    application.state.access_token_manager = AccessTokenManager.from_settings(
        resolved_settings
    )
    install_error_handlers(application)
    application.include_router(health_router)
    application.include_router(auth_router)
    application.include_router(audit_events_router)
    application.include_router(organization_members_router)
    application.include_router(tickets_router)
    return application


app = create_app()
