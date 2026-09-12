from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.documents.models import (
    Document,
    DocumentIngestionIntent,
    DocumentMediaType,
    DocumentProcessingStatus,
    DocumentVersion,
)
from app.documents.outbox import IngestionIntentRepository, stage_upload_with_intent
from app.identity.models import MembershipRole, Organization, OrganizationMember, User

pytestmark = pytest.mark.integration


def tenant(engine: Engine) -> tuple[UUID, UUID]:
    with Session(engine) as session:
        organization = Organization(name="Outbox test", slug=f"outbox-{uuid4().hex}")
        user = User(email=f"{uuid4().hex}@example.com", password_hash="test-only")
        session.add(
            OrganizationMember(
                organization=organization, user=user, role=MembershipRole.ADMIN
            )
        )
        session.flush()
        identifiers = organization.id, user.id
        session.commit()
        return identifiers


def upload(organization_id: UUID, user_id: UUID) -> tuple[Document, DocumentVersion]:
    document = Document(
        id=uuid4(),
        organization_id=organization_id,
        created_by_user_id=user_id,
        display_filename="guide.txt",
    )
    version = DocumentVersion(
        id=uuid4(),
        organization_id=organization_id,
        document_id=document.id,
        version_number=1,
        media_type=DocumentMediaType.TEXT,
        size_bytes=4,
        content_sha256="a" * 64,
        storage_key=f"test/{uuid4()}",
        status=DocumentProcessingStatus.QUEUED,
        attempt_count=0,
    )
    return document, version


def persist(engine: Engine) -> tuple[UUID, UUID, UUID, str]:
    organization_id, user_id = tenant(engine)
    document, version = upload(organization_id, user_id)
    with Session(engine) as session:
        intent = stage_upload_with_intent(
            session, organization_id, document=document, version=version
        )
        identifiers = organization_id, version.id, intent.id, intent.task_id
        session.commit()
    return identifiers


def test_upload_foundation_commits_version_audit_and_stable_intent(
    database_engine: Engine,
):
    organization_id, version_id, intent_id, task_id = persist(database_engine)
    with Session(database_engine) as session:
        intent = IngestionIntentRepository(session, organization_id).get_for_version(
            version_id
        )
        assert (
            intent is not None and intent.id == intent_id and intent.task_id == task_id
        )
        assert UUID(task_id)
        version = session.get(DocumentVersion, version_id)
        assert version is not None and version.status == DocumentProcessingStatus.QUEUED
        assert version.processing_task_id is None  # intent is not worker ownership
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 1


def test_failure_after_real_flush_rolls_back_all_rows(database_engine: Engine):
    organization_id, user_id = tenant(database_engine)
    document, version = upload(organization_id, user_id)
    with Session(database_engine) as session:
        with pytest.raises(RuntimeError, match="after flush"):
            with session.begin():
                stage_upload_with_intent(
                    session, organization_id, document=document, version=version
                )
                # SQL queries prove all writes reached PostgreSQL before failure.
                for model in (
                    Document,
                    DocumentVersion,
                    AuditEvent,
                    DocumentIngestionIntent,
                ):
                    assert session.scalar(select(func.count()).select_from(model)) == 1
                raise RuntimeError("after flush")
    # Independent session, not the rolled-back identity map or a savepoint.
    with Session(database_engine) as fresh:
        for model in (Document, DocumentVersion, AuditEvent, DocumentIngestionIntent):
            assert fresh.scalar(select(func.count()).select_from(model)) == 0


def test_tenant_scoped_read_and_database_composite_fk(database_engine: Engine):
    organization_id, version_id, _, _ = persist(database_engine)
    other_id, _ = tenant(database_engine)
    with Session(database_engine) as session:
        assert (
            IngestionIntentRepository(session, other_id).get_for_version(version_id)
            is None
        )
        assert IngestionIntentRepository(session, organization_id).get_for_version(
            version_id
        )
        # Bypass repository to prove the database, not just Python, rejects IDOR.
        session.add(
            DocumentIngestionIntent(
                organization_id=other_id,
                document_version_id=version_id,
                task_id=str(uuid4()),
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


def test_duplicate_logical_intent_rejected(database_engine: Engine):
    organization_id, version_id, _, task_id = persist(database_engine)
    with Session(database_engine) as session:
        IngestionIntentRepository(session, organization_id).add(
            version_id=version_id, task_id=str(uuid4())
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()
    with Session(database_engine) as fresh:
        intent = IngestionIntentRepository(fresh, organization_id).get_for_version(
            version_id
        )
        assert intent is not None and intent.task_id == task_id


def test_task_id_cannot_be_shared_between_versions(database_engine: Engine):
    _, _, _, task_id = persist(database_engine)
    organization_id, user_id = tenant(database_engine)
    document, version = upload(organization_id, user_id)
    with Session(database_engine) as session:
        session.add_all([document, version])
        session.flush()
        IngestionIntentRepository(session, organization_id).add(
            version_id=version.id, task_id=task_id
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


@pytest.mark.parametrize(
    "column,expression",
    [
        ("id", "gen_random_uuid()"),
        ("organization_id", "gen_random_uuid()"),
        ("document_version_id", "gen_random_uuid()"),
        ("task_id", "'replacement-task'"),
        ("created_at", "created_at + interval '1 second'"),
    ],
)
def test_raw_sql_cannot_rewrite_intent_identity(
    database_engine: Engine, column: str, expression: str
):
    _, _, intent_id, task_id = persist(database_engine)
    with pytest.raises(DBAPIError, match="identity is immutable"):
        with database_engine.begin() as connection:
            connection.execute(
                text(
                    f"UPDATE document_ingestion_outbox SET {column} = {expression} WHERE id = :id"
                ),
                {"id": intent_id},
            )
    with Session(database_engine) as fresh:
        intent = fresh.get(DocumentIngestionIntent, intent_id)
        assert intent is not None and intent.task_id == task_id


@pytest.mark.parametrize("task_id", ["", " leading", "trailing "])
def test_database_rejects_empty_or_padded_task_id(
    database_engine: Engine, task_id: str
):
    organization_id, user_id = tenant(database_engine)
    document, version = upload(organization_id, user_id)
    with Session(database_engine) as session:
        session.add_all([document, version])
        session.flush()
        session.add(
            DocumentIngestionIntent(
                organization_id=organization_id,
                document_version_id=version.id,
                task_id=task_id,
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


def test_cross_tenant_helper_rejects_before_staging(database_engine: Engine):
    organization_id, user_id = tenant(database_engine)
    document, version = upload(organization_id, user_id)
    with Session(database_engine) as session:
        with pytest.raises(ValueError, match="tenant-matched"):
            stage_upload_with_intent(
                session, uuid4(), document=document, version=version
            )
        assert not session.new


def test_downgrade_retains_outstanding_intents(
    database_engine: Engine, migrated_database_url: str
):
    _, _, intent_id, _ = persist(database_engine)
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", migrated_database_url.replace("%", "%%"))
    with pytest.raises(DBAPIError, match="intents remain"):
        command.downgrade(config, "0004_documents")
    with Session(database_engine) as fresh:
        assert fresh.get(DocumentIngestionIntent, intent_id) is not None
        assert (
            fresh.scalar(text("SELECT version_num FROM alembic_version"))
            == "0005_ingestion_outbox"
        )
