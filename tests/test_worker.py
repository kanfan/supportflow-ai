import ssl
from typing import Any, cast

from pydantic import SecretStr

from app.config import Settings
from app.worker import HEALTH_TASK_NAME, create_celery


def test_health_task_runs_in_eager_mode() -> None:
    application = create_celery(
        Settings(
            environment="test",
            celery_broker_url=SecretStr("memory://"),
            celery_result_backend_url=SecretStr("cache+memory://"),
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
            celery_broker_url=SecretStr("redis://queue.example:6379/4"),
            celery_result_backend_url=SecretStr("redis://queue.example:6379/5"),
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


def test_deployed_celery_forces_full_redis_certificate_verification() -> None:
    settings = Settings(
        environment="staging",
        auth_secret_key=SecretStr("staging-secret-with-at-least-thirty-two-bytes"),
        document_scanner_mode="external",
        document_storage_mode="s3",
        document_s3_bucket="supportflow-staging-documents",
        document_s3_region="eu-central-1",
        redis_url=SecretStr("rediss://default:secret@cache.internal:6379/0"),
        celery_broker_url=SecretStr("rediss://default:secret@cache.internal:6379/1"),
        celery_result_backend_url=SecretStr(
            "rediss://default:secret@cache.internal:6379/2"
        ),
    )

    application = create_celery(settings)

    assert application.conf.broker_use_ssl == {
        "ssl_cert_reqs": ssl.CERT_REQUIRED,
        "ssl_check_hostname": True,
    }
    assert application.conf.redis_backend_use_ssl == {
        "ssl_cert_reqs": ssl.CERT_REQUIRED,
        "ssl_check_hostname": True,
    }
    assert application.connection_for_write().ssl == {
        "ssl_cert_reqs": ssl.CERT_REQUIRED,
        "ssl_check_hostname": True,
    }
    backend = cast(Any, application.backend)
    backend_options = backend.client.connection_pool.connection_kwargs
    assert backend_options["ssl_cert_reqs"] == ssl.CERT_REQUIRED
    assert backend_options["ssl_check_hostname"] is True
