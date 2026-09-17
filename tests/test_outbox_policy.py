import pytest
from app.config import Settings
from app.documents.relay import RelayPolicy


def test_default_recovery_exceeds_worker_and_broker_windows():
    settings = Settings(
        environment="test",
        outbox_recovery_seconds=1200,
        celery_visibility_timeout_seconds=180,
        document_ingestion_max_retries=3,
        document_ingestion_hard_time_limit_seconds=135,
    )
    assert settings.outbox_effective_recovery_seconds == 1200
    assert settings.outbox_effective_recovery_seconds > (
        settings.celery_visibility_timeout_seconds
        + settings.document_ingestion_hard_time_limit_seconds * 4
        + settings.document_ingestion_max_retries * 60
    )
    assert RelayPolicy().lease_seconds == 30
    assert RelayPolicy().max_recoveries == 3


@pytest.mark.parametrize("visibility", [1200, 3600, 86400])
def test_recovery_floor_tracks_increased_visibility(visibility):
    settings = Settings(
        environment="test",
        celery_visibility_timeout_seconds=visibility,
        document_ingestion_max_retries=3,
        document_ingestion_hard_time_limit_seconds=135,
    )
    assert settings.outbox_effective_recovery_seconds > visibility + 540 + 180
