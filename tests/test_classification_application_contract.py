from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.audit.service import AuditEventService, AuditMetadataError
from app.classification.application import FakeClassificationRuntime
from app.classification.models import ClassificationOperation
from app.classification.providers import FakeClassificationProvider
from app.config import Settings
from app.infrastructure.database import Base
from app.main import create_app


def fake(
    response: str = '{"outcome":"classified","category":"billing"}',
) -> FakeClassificationProvider:
    return FakeClassificationProvider("test", {"test": response})


def test_runtime_digest_tracks_script_and_config_version() -> None:
    first = FakeClassificationRuntime(fake())
    assert first.digest == FakeClassificationRuntime(fake()).digest
    assert first.digest != FakeClassificationRuntime(fake(), "v2").digest
    assert (
        first.digest
        != FakeClassificationRuntime(
            fake('{"outcome":"insufficient_context","category":null}')
        ).digest
    )
    assert "billing" not in first.digest


@pytest.mark.parametrize("version", ["", "x" * 101])
def test_configuration_version_is_bounded(version: str) -> None:
    with pytest.raises(ValueError):
        FakeClassificationRuntime(fake(), version)


def test_default_application_has_no_classification_provider() -> None:
    app = create_app(Settings(environment="test"))
    assert app.state.classification_runtime is None
    app.state.session_factory.kw["bind"].dispose()


def test_fake_cannot_be_injected_into_deployed_environment() -> None:
    # model_copy avoids unrelated TLS settings validation to target the factory guard.
    settings = Settings(environment="test").model_copy(
        update={"environment": "staging"}
    )
    with pytest.raises(ValueError, match="local/test"):
        create_app(settings, classification_runtime=FakeClassificationRuntime(fake()))


def test_classification_table_has_tenant_actor_and_ticket_keys() -> None:
    table = Base.metadata.tables[ClassificationOperation.__tablename__]
    keys = {tuple(fk.column_keys) for fk in table.foreign_key_constraints}
    assert ("organization_id", "requesting_actor_user_id") in keys
    assert ("organization_id", "ticket_id") in keys
    assert not table.c.requesting_actor_user_id.nullable
    assert not {"body", "subject", "prompt", "raw_response"} & set(table.c.keys())


def test_classification_audit_allowlist() -> None:
    with Session() as session:
        service = AuditEventService(session, uuid4())
        event = service.record_classification_finished(
            actor_user_id=uuid4(), operation_id=uuid4(), state="unknown"
        )
        assert event.event_metadata == {"mode": "fake", "state": "unknown"}
        with pytest.raises(AuditMetadataError):
            service.record_classification_finished(
                actor_user_id=uuid4(), operation_id=uuid4(), state="raw provider text"
            )
