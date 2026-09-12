from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.documents.models import DocumentIngestionIntent
from app.documents.outbox import IngestionIntentRepository


@pytest.mark.parametrize("task_id", ["", " ", " padded", "padded ", "x" * 256])
def test_repository_rejects_invalid_identity_without_database(task_id: str):
    with Session() as session:
        with pytest.raises(ValueError):
            IngestionIntentRepository(session, uuid4()).add(
                version_id=uuid4(), task_id=task_id
            )
        assert not session.new


def test_repository_preserves_legacy_task_id_without_committing():
    with Session() as session:
        intent = IngestionIntentRepository(session, uuid4()).add(
            version_id=uuid4(), task_id="legacy-task-identity"
        )
        assert intent.task_id == "legacy-task-identity"
        assert intent in session.new


def test_intent_contains_no_document_body_or_storage_credentials():
    assert set(DocumentIngestionIntent.__table__.c.keys()) == {
        "id",
        "organization_id",
        "document_version_id",
        "task_id",
        "created_at",
    }
