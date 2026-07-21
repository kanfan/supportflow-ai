from celery import Celery

from app.config import Settings, get_settings


HEALTH_TASK_NAME = "supportflow.health.ping"


def health_ping() -> dict[str, str]:
    return {"status": "ok"}


def create_celery(settings: Settings | None = None) -> Celery:
    resolved_settings = settings or get_settings()
    application = Celery(
        "supportflow",
        broker=resolved_settings.celery_broker_url,
        backend=resolved_settings.celery_result_backend_url,
    )
    application.conf.update(
        accept_content=["json"],
        broker_connection_retry_on_startup=True,
        result_serializer="json",
        task_serializer="json",
        timezone="UTC",
    )
    application.task(name=HEALTH_TASK_NAME)(health_ping)
    return application


celery_app = create_celery()
