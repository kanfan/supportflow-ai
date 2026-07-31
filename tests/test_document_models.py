from app.documents.models import (
    Document,
    DocumentErrorCode,
    DocumentMediaType,
    DocumentProcessingStatus,
    DocumentSourceType,
    DocumentVersion,
    MAX_DOCUMENT_BYTES,
)


def test_document_model_uses_version_owned_processing_state() -> None:
    assert "status" not in Document.__table__.c
    assert "storage_key" not in Document.__table__.c
    assert "extracted_text" not in Document.__table__.c
    assert {
        "status",
        "storage_key",
        "extracted_text",
        "processing_task_id",
        "processing_started_at",
        "attempt_count",
        "error_code",
    } <= set(DocumentVersion.__table__.c.keys())


def test_document_model_exposes_closed_week4_vocabulary() -> None:
    assert {source.value for source in DocumentSourceType} == {"upload"}
    assert {media_type.value for media_type in DocumentMediaType} == {
        "application/pdf",
        "text/plain",
        "text/markdown",
    }
    assert {status.value for status in DocumentProcessingStatus} == {
        "queued",
        "extracting",
        "ready",
        "failed",
    }
    assert DocumentErrorCode.DISPATCH_FAILED.value == "dispatch_failed"


def test_document_version_database_limit_matches_accepted_contract() -> None:
    assert MAX_DOCUMENT_BYTES == 10 * 1024 * 1024


def test_sensitive_document_fields_are_not_on_the_stable_document() -> None:
    public_document_fields = set(Document.__table__.c.keys())
    assert "content_sha256" not in public_document_fields
    assert "processing_task_id" not in public_document_fields
    assert "error_code" not in public_document_fields
