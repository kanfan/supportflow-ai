from fastapi import FastAPI

from app.api.health import router as health_router
from app.config import Settings, get_settings
from app.errors import install_error_handlers


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    application = FastAPI(
        title=resolved_settings.app_name,
        version=resolved_settings.app_version,
        debug=resolved_settings.debug,
    )
    application.state.settings = resolved_settings
    install_error_handlers(application)
    application.include_router(health_router)
    return application


app = create_app()
