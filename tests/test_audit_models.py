from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.audit.models import AuditAction, AuditEvent, AuditResourceType
from app.audit.service import AuditEventService, AuditMetadataError


def test_audit_model_maps_reserved_metadata_column_without_update_timestamp() -> None:
    assert AuditEvent.__table__.c.metadata.name == "metadata"
    assert "event_metadata" not in AuditEvent.__table__.c
    assert hasattr(AuditEvent, "event_metadata")
    assert not hasattr(AuditEvent, "updated_at")


def test_initial_audit_vocabulary_is_explicit_and_stable() -> None:
    assert {action.value for action in AuditAction} == {
        "organization_member.created",
        "ticket.created",
        "ticket_message.created",
        "ticket.status_changed",
    }
    assert {resource.value for resource in AuditResourceType} == {
        "organization_member",
        "ticket",
        "ticket_message",
    }


@pytest.mark.parametrize(
    ("method_name", "kwargs"),
    [
        (
            "record_organization_member_created",
            {
                "actor_user_id": uuid4(),
                "member_user_id": uuid4(),
                "role": "owner",
            },
        ),
        (
            "record_ticket_created",
            {
                "actor_user_id": uuid4(),
                "ticket_id": uuid4(),
                "source_type": "email",
            },
        ),
        (
            "record_ticket_status_changed",
            {
                "actor_user_id": uuid4(),
                "ticket_id": uuid4(),
                "previous_status": "open",
                "new_status": "reopened",
            },
        ),
    ],
)
def test_action_specific_methods_reject_unapproved_metadata_values(
    method_name: str,
    kwargs: dict[str, object],
) -> None:
    with Session() as session:
        service = AuditEventService(session, uuid4())
        method = getattr(service, method_name)

        with pytest.raises(AuditMetadataError):
            method(**kwargs)


def test_ticket_audit_interface_only_builds_allowlisted_metadata() -> None:
    organization_id = uuid4()
    actor_user_id = uuid4()
    ticket_id = uuid4()

    with Session() as session:
        service = AuditEventService(session, organization_id)
        created = service.record_ticket_created(
            actor_user_id=actor_user_id,
            ticket_id=ticket_id,
            source_type="manual",
        )
        transitioned = service.record_ticket_status_changed(
            actor_user_id=actor_user_id,
            ticket_id=ticket_id,
            previous_status="open",
            new_status="processing",
        )
        message = service.record_ticket_message_created(
            actor_user_id=actor_user_id,
            ticket_message_id=uuid4(),
        )

    assert created.event_metadata == {"source_type": "manual"}
    assert transitioned.event_metadata == {
        "previous_status": "open",
        "new_status": "processing",
    }
    assert message.event_metadata == {}
