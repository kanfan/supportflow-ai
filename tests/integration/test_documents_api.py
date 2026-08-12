from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from hashlib import sha256
import logging
from typing import Any, BinaryIO
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest
from pydantic import SecretStr
from sqlalchemy import func, inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.models import AuditAction, AuditEvent
from app.audit.service import AuditEventService
from app.config import Settings
from app.documents.models import (
    Document,
    DocumentMediaType,
    DocumentProcessingStatus,
    DocumentSourceType,
    DocumentVersion,
    MAX_DOCUMENT_BYTES,
)
from app.documents.storage import InMemoryDocumentStorage
from app.identity.models import (
    MembershipRole,
    Organization,
    OrganizationMember,
    User,
)
from app.main import create_app


pytestmark = pytest.mark.integration
TEST_AUTH_SECRET = "document-integration-secret-with-thirty-two-bytes"
PASSWORD = "correct horse battery staple"
ORIGINAL_RECORD_DOCUMENT_UPLOADED = AuditEventService.record_document_uploaded


@dataclass
class RecordingDispatcher:
    dispatched: list[UUID] = field(default_factory=list)
    failure: Exception | None = None

    def dispatch(self, document_version_id: UUID) -> None:
        self.dispatched.append(document_version_id)
        if self.failure is not None:
            raise self.failure


@dataclass
class DocumentHarness:
    client: TestClient
    storage: InMemoryDocumentStorage
    dispatcher: RecordingDispatcher


@pytest.fixture
def document_harness(migrated_database_url: str) -> Iterator[DocumentHarness]:
    storage = InMemoryDocumentStorage()
    dispatcher = RecordingDispatcher()
    application = create_app(
        Settings(
            environment="test",
            database_url=SecretStr(migrated_database_url),
            auth_secret_key=SecretStr(TEST_AUTH_SECRET),
            auth_issuer="supportflow-document-integration-test",
            auth_audience="supportflow-api-document-integration-test",
        ),
        document_storage=storage,
        document_task_dispatcher=dispatcher,
    )
    with TestClient(application) as client:
        yield DocumentHarness(
            client=client,
            storage=storage,
            dispatcher=dispatcher,
        )


def registration_payload(prefix: str) -> dict[str, str]:
    return {
        "email": f"{prefix}@example.com",
        "password": PASSWORD,
        "organization_name": f"{prefix} Organization",
        "organization_slug": prefix,
    }


def register_and_login(
    client: TestClient,
    prefix: str,
) -> tuple[dict[str, Any], str]:
    registration_response = client.post(
        "/api/v1/auth/register",
        json=registration_payload(prefix),
    )
    assert registration_response.status_code == 201, registration_response.text
    registration = registration_response.json()

    login_response = client.post(
        "/api/v1/auth/login",
        json={
            "email": f"{prefix}@example.com",
            "password": PASSWORD,
        },
    )
    assert login_response.status_code == 200, login_response.text
    return registration, str(login_response.json()["access_token"])


def tenant_headers(token: str, organization_id: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-Organization-ID": organization_id,
    }


def upload(
    harness: DocumentHarness,
    *,
    token: str,
    organization_id: str,
    filename: str = "guide.md",
    content: bytes = b"# Safe support guide",
    content_type: str = "application/octet-stream",
    data: dict[str, str] | None = None,
):
    return harness.client.post(
        "/api/v1/documents",
        headers=tenant_headers(token, organization_id),
        files={"file": (filename, content, content_type)},
        data=data,
    )


def test_admin_upload_persists_private_version_and_safe_audit(
    document_harness: DocumentHarness,
    database_engine: Engine,
) -> None:
    prefix = f"document-success-{uuid4().hex}"
    registration, token = register_and_login(document_harness.client, prefix)
    organization_id = registration["organization"]["id"]
    user_id = registration["user"]["id"]
    content = b"# private-message-canary\n\nSafe guide."

    response = upload(
        document_harness,
        token=token,
        organization_id=organization_id,
        filename="Support Guide.MD",
        content=content,
    )

    assert response.status_code == 202, response.text
    body = response.json()
    assert response.headers["location"] == f"/api/v1/documents/{body['id']}"
    assert body["source_type"] == "upload"
    assert body["display_filename"] == "Support Guide.md"
    assert body["version"]["version_number"] == 1
    assert body["version"]["media_type"] == "text/markdown"
    assert body["version"]["size_bytes"] == len(content)
    assert body["version"]["status"] == "queued"
    assert body["version"]["error_code"] is None
    assert body["version"]["error_message"] is None
    assert "organization_id" not in body
    assert "storage_key" not in response.text
    assert "content_sha256" not in response.text
    assert "private-message-canary" not in response.text

    version_id = UUID(body["version"]["id"])
    assert document_harness.dispatcher.dispatched == [version_id]

    with Session(database_engine) as session:
        document = session.get(Document, UUID(body["id"]))
        version = session.get(DocumentVersion, version_id)
        events = list(
            session.scalars(
                select(AuditEvent).where(
                    AuditEvent.organization_id == UUID(organization_id)
                )
            )
        )

    assert document is not None
    assert version is not None
    assert document.created_by_user_id == UUID(user_id)
    assert document.source_type is DocumentSourceType.UPLOAD
    assert version.status is DocumentProcessingStatus.QUEUED
    assert version.content_sha256 == sha256(content).hexdigest()
    assert version.storage_key == (
        f"organizations/{organization_id}/documents/{document.id}/versions/{version.id}"
    )
    assert document_harness.storage.objects[version.storage_key] == content
    assert [(event.action, event.resource_id) for event in events] == [
        (AuditAction.DOCUMENT_UPLOADED.value, document.id)
    ]
    assert events[0].event_metadata == {
        "version_number": 1,
        "media_type": "text/markdown",
        "size_bytes": len(content),
    }
    serialized_audit = str(events[0].event_metadata)
    assert document.display_filename not in serialized_audit
    assert version.storage_key not in serialized_audit
    assert version.content_sha256 not in serialized_audit
    assert content.decode() not in serialized_audit


def test_status_lookup_is_admin_only_and_tenant_safe(
    document_harness: DocumentHarness,
    database_engine: Engine,
) -> None:
    first_registration, first_token = register_and_login(
        document_harness.client,
        f"document-tenant-a-{uuid4().hex}",
    )
    second_registration, second_token = register_and_login(
        document_harness.client,
        f"document-tenant-b-{uuid4().hex}",
    )
    created = upload(
        document_harness,
        token=first_token,
        organization_id=first_registration["organization"]["id"],
    )
    assert created.status_code == 202
    document_id = created.json()["id"]

    own_status = document_harness.client.get(
        f"/api/v1/documents/{document_id}",
        headers=tenant_headers(
            first_token,
            first_registration["organization"]["id"],
        ),
    )
    cross_tenant = document_harness.client.get(
        f"/api/v1/documents/{document_id}",
        headers=tenant_headers(
            second_token,
            second_registration["organization"]["id"],
        ),
    )
    missing = document_harness.client.get(
        f"/api/v1/documents/{uuid4()}",
        headers=tenant_headers(
            second_token,
            second_registration["organization"]["id"],
        ),
    )
    assert own_status.status_code == 200
    assert cross_tenant.status_code == 404
    assert cross_tenant.json() == missing.json()

    agent_registration, agent_token = register_and_login(
        document_harness.client,
        f"document-agent-{uuid4().hex}",
    )
    with Session(database_engine) as session:
        session.add(
            OrganizationMember(
                organization_id=first_registration["organization"]["id"],
                user_id=agent_registration["user"]["id"],
                role=MembershipRole.AGENT,
            )
        )
        session.commit()

    agent_upload = upload(
        document_harness,
        token=agent_token,
        organization_id=first_registration["organization"]["id"],
    )
    agent_status = document_harness.client.get(
        f"/api/v1/documents/{document_id}",
        headers=tenant_headers(
            agent_token,
            first_registration["organization"]["id"],
        ),
    )
    assert agent_upload.status_code == 403
    assert agent_status.status_code == 403


@pytest.mark.parametrize("authorization", [None, "Bearer invalid-token"])
def test_document_endpoints_require_valid_authentication(
    document_harness: DocumentHarness,
    authorization: str | None,
) -> None:
    headers = {"X-Organization-ID": str(uuid4())}
    if authorization is not None:
        headers["Authorization"] = authorization

    upload_response = document_harness.client.post(
        "/api/v1/documents",
        headers=headers,
        files={"file": ("guide.txt", b"safe", "text/plain")},
    )
    status_response = document_harness.client.get(
        f"/api/v1/documents/{uuid4()}",
        headers=headers,
    )

    for response in (upload_response, status_response):
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"


def test_upload_rejects_extra_multipart_fields_and_invalid_content(
    document_harness: DocumentHarness,
) -> None:
    registration, token = register_and_login(
        document_harness.client,
        f"document-invalid-{uuid4().hex}",
    )
    organization_id = registration["organization"]["id"]

    extra_field = upload(
        document_harness,
        token=token,
        organization_id=organization_id,
        data={"organization_id": str(uuid4())},
    )
    wrong_pdf = upload(
        document_harness,
        token=token,
        organization_id=organization_id,
        filename="manual.pdf",
        content=b"plain text pretending to be PDF",
        content_type="application/pdf",
    )
    binary_text = upload(
        document_harness,
        token=token,
        organization_id=organization_id,
        filename="manual.txt",
        content=b"unsafe\x00binary",
        content_type="text/plain",
    )
    pdf_as_text = upload(
        document_harness,
        token=token,
        organization_id=organization_id,
        filename="manual.txt",
        content=b"%PDF-1.7\nPDF disguised as text",
        content_type="text/plain",
    )
    pdf_as_markdown = upload(
        document_harness,
        token=token,
        organization_id=organization_id,
        filename="manual.md",
        content=b"%PDF-1.7\nPDF disguised as Markdown",
        content_type="text/markdown",
    )

    assert extra_field.status_code == 422
    assert extra_field.json()["error"]["code"] == "invalid_document_form"
    for response in (
        wrong_pdf,
        binary_text,
        pdf_as_text,
        pdf_as_markdown,
    ):
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "unsupported_document_type"

    assert document_harness.storage.objects == {}
    assert document_harness.dispatcher.dispatched == []


def test_upload_enforces_exact_ten_mibibyte_boundary(
    document_harness: DocumentHarness,
) -> None:
    registration, token = register_and_login(
        document_harness.client,
        f"document-size-{uuid4().hex}",
    )
    organization_id = registration["organization"]["id"]

    exact = upload(
        document_harness,
        token=token,
        organization_id=organization_id,
        filename="exact.txt",
        content=b"x" * MAX_DOCUMENT_BYTES,
    )
    too_large = upload(
        document_harness,
        token=token,
        organization_id=organization_id,
        filename="too-large.txt",
        content=b"x" * (MAX_DOCUMENT_BYTES + 1),
    )

    assert exact.status_code == 202
    assert too_large.status_code == 413
    assert too_large.json()["error"]["code"] == "document_too_large"
    assert len(document_harness.storage.objects) == 1
    assert len(document_harness.dispatcher.dispatched) == 1


class FailingPutStorage(InMemoryDocumentStorage):
    def put(self, key: str, source: BinaryIO) -> None:
        del key, source
        raise RuntimeError("private storage unavailable")


class FailingDeleteStorage(InMemoryDocumentStorage):
    def delete(self, key: str) -> None:
        del key
        raise RuntimeError("cleanup implementation detail")


def add_database_invalid_upload_audit(
    service: AuditEventService,
    *,
    actor_user_id: UUID,
    document_id: UUID,
    version_number: int,
    media_type: DocumentMediaType,
    size_bytes: int,
) -> AuditEvent:
    event = ORIGINAL_RECORD_DOCUMENT_UPLOADED(
        service,
        actor_user_id=actor_user_id,
        document_id=document_id,
        version_number=version_number,
        media_type=media_type,
        size_bytes=size_bytes,
    )
    event.action = "invalid_without_required_dot"
    return event


def test_storage_failure_creates_no_database_or_queue_state(
    document_harness: DocumentHarness,
    database_engine: Engine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="app.documents.service")
    registration, token = register_and_login(
        document_harness.client,
        f"document-storage-fail-{uuid4().hex}",
    )
    document_harness.client.app.state.document_storage = FailingPutStorage()

    response = upload(
        document_harness,
        token=token,
        organization_id=registration["organization"]["id"],
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "document_storage_unavailable"
    assert document_harness.dispatcher.dispatched == []
    assert "error_category=storage_unavailable" in caplog.text
    assert "RuntimeError" not in caplog.text
    assert "private storage unavailable" not in caplog.text
    with Session(database_engine) as session:
        assert session.scalar(select(func.count()).select_from(Document)) == 0
        assert session.scalar(select(func.count()).select_from(DocumentVersion)) == 0


def test_database_commit_failure_after_storage_put_cleans_up_everything(
    document_harness: DocumentHarness,
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registration, token = register_and_login(
        document_harness.client,
        f"document-db-fail-{uuid4().hex}",
    )

    monkeypatch.setattr(
        AuditEventService,
        "record_document_uploaded",
        add_database_invalid_upload_audit,
    )
    response = upload(
        document_harness,
        token=token,
        organization_id=registration["organization"]["id"],
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "document_persistence_failed"
    assert document_harness.storage.objects == {}
    assert document_harness.dispatcher.dispatched == []
    with Session(database_engine) as session:
        assert session.scalar(select(func.count()).select_from(Document)) == 0
        assert session.scalar(select(func.count()).select_from(DocumentVersion)) == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == AuditAction.DOCUMENT_UPLOADED.value)
            )
            == 0
        )


def test_cleanup_failure_uses_safe_stable_log_category(
    document_harness: DocumentHarness,
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="app.documents.service")
    registration, token = register_and_login(
        document_harness.client,
        f"document-cleanup-fail-{uuid4().hex}",
    )
    storage = FailingDeleteStorage()
    document_harness.client.app.state.document_storage = storage
    monkeypatch.setattr(
        AuditEventService,
        "record_document_uploaded",
        add_database_invalid_upload_audit,
    )

    content = b"cleanup-secret-content"
    filename = "cleanup-secret-filename.txt"
    response = upload(
        document_harness,
        token=token,
        organization_id=registration["organization"]["id"],
        filename=filename,
        content=content,
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "document_persistence_failed"
    assert len(storage.objects) == 1
    assert "error_category=storage_cleanup_failed" in caplog.text
    assert "RuntimeError" not in caplog.text
    assert "cleanup implementation detail" not in caplog.text
    assert filename not in caplog.text
    assert content.decode() not in caplog.text
    assert next(iter(storage.objects)) not in caplog.text
    with Session(database_engine) as session:
        assert session.scalar(select(func.count()).select_from(Document)) == 0
        assert session.scalar(select(func.count()).select_from(DocumentVersion)) == 0


def test_dispatch_failure_becomes_safe_inspectable_failed_state(
    document_harness: DocumentHarness,
    database_engine: Engine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="app.documents.service")
    registration, token = register_and_login(
        document_harness.client,
        f"document-dispatch-fail-{uuid4().hex}",
    )
    organization_id = registration["organization"]["id"]
    content = b"dispatch-secret-message-canary"
    filename = "dispatch-sensitive-name.txt"
    document_harness.dispatcher.failure = RuntimeError(
        "broker failure with raw internal detail"
    )

    response = upload(
        document_harness,
        token=token,
        organization_id=organization_id,
        filename=filename,
        content=content,
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "dispatch_failed"
    document_id = response.json()["error"]["details"]["document_id"]
    assert response.headers["location"] == f"/api/v1/documents/{document_id}"
    assert "broker failure" not in response.text
    assert content.decode() not in response.text

    status_response = document_harness.client.get(
        response.headers["location"],
        headers=tenant_headers(token, organization_id),
    )
    assert status_response.status_code == 200
    assert status_response.json()["version"]["status"] == "failed"
    assert status_response.json()["version"]["error_code"] == "dispatch_failed"
    assert status_response.json()["version"]["error_message"] == (
        "Document processing could not be queued"
    )

    with Session(database_engine) as session:
        version = session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.document_id == UUID(document_id)
            )
        )
        actions = list(
            session.scalars(
                select(AuditEvent.action)
                .where(AuditEvent.organization_id == UUID(organization_id))
                .order_by(AuditEvent.created_at, AuditEvent.id)
            )
        )
    assert version is not None
    assert actions == [
        AuditAction.DOCUMENT_UPLOADED.value,
        AuditAction.DOCUMENT_FAILED.value,
    ]
    assert filename not in caplog.text
    assert content.decode() not in caplog.text
    assert version.storage_key not in caplog.text
    assert "error_category=dispatch_failed" in caplog.text
    assert "RuntimeError" not in caplog.text
    assert "broker failure with raw internal detail" not in caplog.text


def test_database_enforces_document_tenant_and_version_constraints(
    database_session: Session,
) -> None:
    first_organization = Organization(
        name="Document First",
        slug=f"document-first-{uuid4().hex}",
    )
    second_organization = Organization(
        name="Document Second",
        slug=f"document-second-{uuid4().hex}",
    )
    first_user = User(
        email=f"document-first-{uuid4().hex}@example.com",
        password_hash="not-a-real-password-hash",
    )
    second_user = User(
        email=f"document-second-{uuid4().hex}@example.com",
        password_hash="not-a-real-password-hash",
    )
    database_session.add_all(
        [
            OrganizationMember(
                organization=first_organization,
                user=first_user,
                role=MembershipRole.ADMIN,
            ),
            OrganizationMember(
                organization=second_organization,
                user=second_user,
                role=MembershipRole.ADMIN,
            ),
        ]
    )
    database_session.flush()

    with pytest.raises(IntegrityError), database_session.begin_nested():
        database_session.add(
            Document(
                organization_id=first_organization.id,
                created_by_user_id=second_user.id,
                display_filename="cross-tenant.txt",
            )
        )
        database_session.flush()

    document = Document(
        organization_id=first_organization.id,
        created_by_user_id=first_user.id,
        display_filename="valid.txt",
    )
    database_session.add(document)
    database_session.flush()

    valid_version = DocumentVersion(
        organization_id=first_organization.id,
        document_id=document.id,
        version_number=1,
        media_type=DocumentMediaType.TEXT,
        size_bytes=4,
        content_sha256=sha256(b"safe").hexdigest(),
        storage_key=f"organizations/{first_organization.id}/{uuid4()}",
    )
    database_session.add(valid_version)
    database_session.flush()

    invalid_versions = [
        DocumentVersion(
            organization_id=second_organization.id,
            document_id=document.id,
            version_number=2,
            media_type=DocumentMediaType.TEXT,
            size_bytes=4,
            content_sha256=sha256(b"safe").hexdigest(),
            storage_key=f"organizations/{second_organization.id}/{uuid4()}",
        ),
        DocumentVersion(
            organization_id=first_organization.id,
            document_id=document.id,
            version_number=2,
            media_type=DocumentMediaType.TEXT,
            size_bytes=0,
            content_sha256=sha256(b"safe").hexdigest(),
            storage_key=f"organizations/{first_organization.id}/{uuid4()}",
        ),
        DocumentVersion(
            organization_id=first_organization.id,
            document_id=document.id,
            version_number=2,
            media_type=DocumentMediaType.TEXT,
            size_bytes=4,
            content_sha256="NOT-A-SHA256",
            storage_key=f"organizations/{first_organization.id}/{uuid4()}",
        ),
        DocumentVersion(
            organization_id=first_organization.id,
            document_id=document.id,
            version_number=1,
            media_type=DocumentMediaType.TEXT,
            size_bytes=4,
            content_sha256=sha256(b"safe").hexdigest(),
            storage_key=f"organizations/{first_organization.id}/{uuid4()}",
        ),
    ]
    for invalid_version in invalid_versions:
        with pytest.raises(IntegrityError), database_session.begin_nested():
            database_session.add(invalid_version)
            database_session.flush()


def test_migration_exposes_expected_document_indexes_and_foreign_keys(
    database_engine: Engine,
) -> None:
    inspector = inspect(database_engine)
    version_indexes = {
        index["name"]: index for index in inspector.get_indexes("document_versions")
    }
    document_foreign_keys = {
        foreign_key["name"] for foreign_key in inspector.get_foreign_keys("documents")
    }
    version_foreign_keys = {
        foreign_key["name"]
        for foreign_key in inspector.get_foreign_keys("document_versions")
    }

    assert {
        "ix_document_versions_organization_document_version",
        "ix_document_versions_organization_status_updated_id",
    } <= set(version_indexes)
    assert version_indexes["ix_document_versions_organization_document_version"].get(
        "column_sorting"
    ) == {"version_number": ("desc",)}
    assert version_indexes["ix_document_versions_organization_status_updated_id"].get(
        "column_sorting"
    ) == {
        "updated_at": ("desc",),
        "id": ("desc",),
    }
    assert "fk_documents_organization_id_organization_members" in (
        document_foreign_keys
    )
    assert "fk_document_versions_organization_id_documents" in (version_foreign_keys)
