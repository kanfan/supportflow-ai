from app.config import Settings
from app.worker import HEALTH_TASK_NAME, create_celery


def test_health_task_runs_in_eager_mode() -> None:
    application = create_celery(
        Settings(
            environment="test",
            celery_broker_url="memory://",
            celery_result_backend_url="cache+memory://",
        )
    )
    application.conf.task_always_eager = True
    application.conf.task_store_eager_result = True

    result = application.tasks[HEALTH_TASK_NAME].apply()

    assert result.successful()
    assert result.get() == {"status": "ok"}


def test_celery_uses_configured_redis_urls() -> None:
    application = create_celery(
        Settings(
            environment="test",
            celery_broker_url="redis://queue.example:6379/4",
            celery_result_backend_url="redis://queue.example:6379/5",
            celery_visibility_timeout_seconds=240,
        )
    )

    assert application.conf.broker_url == "redis://queue.example:6379/4"
    assert application.conf.result_backend == "redis://queue.example:6379/5"
    assert application.conf.broker_transport_options == {"visibility_timeout": 240}
    assert application.conf.result_backend_transport_options == {
        "visibility_timeout": 240
    }
    assert application.conf.visibility_timeout == 240
