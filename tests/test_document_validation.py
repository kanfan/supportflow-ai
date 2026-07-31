from hashlib import sha256
from io import BytesIO

import pytest

from app.documents.models import DocumentMediaType
from app.documents.validation import (
    DocumentTooLargeError,
    EmptyDocumentError,
    UnsafeDocumentFilenameError,
    UnsupportedDocumentTypeError,
    normalize_display_filename,
    stage_document_upload,
)


@pytest.mark.parametrize(
    ("filename", "content", "expected_name", "expected_media_type"),
    [
        (
            "Manual.PDF",
            b"%PDF-1.7\nsafe fixture",
            "Manual.pdf",
            DocumentMediaType.PDF,
        ),
        (
            "notes.TXT",
            "Türkçe destek notu".encode(),
            "notes.txt",
            DocumentMediaType.TEXT,
        ),
        (
            "guide.MD",
            b"# Support guide\n\nSafe content.",
            "guide.md",
            DocumentMediaType.MARKDOWN,
        ),
        (
            "guide.markdown",
            b"# Support guide",
            "guide.markdown",
            DocumentMediaType.MARKDOWN,
        ),
    ],
)
def test_stage_document_upload_detects_supported_content(
    filename: str,
    content: bytes,
    expected_name: str,
    expected_media_type: DocumentMediaType,
) -> None:
    staged = stage_document_upload(filename=filename, source=BytesIO(content))
    try:
        assert staged.display_filename == expected_name
        assert staged.media_type is expected_media_type
        assert staged.size_bytes == len(content)
        assert staged.content_sha256 == sha256(content).hexdigest()
        assert staged.stream.read() == content
    finally:
        staged.close()


@pytest.mark.parametrize(
    "filename",
    [
        None,
        "",
        ".",
        "..",
        "../secret.txt",
        r"..\secret.txt",
        "folder/manual.pdf",
        "manual\x00.txt",
        f"{'a' * 201}.txt",
    ],
)
def test_filename_validation_rejects_empty_path_and_control_values(
    filename: str | None,
) -> None:
    with pytest.raises(UnsafeDocumentFilenameError):
        normalize_display_filename(filename)


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("manual.exe", b"safe"),
        ("manual", b"safe"),
        ("manual.pdf", b"not a PDF"),
        ("manual.txt", b"%PDF-1.7\nPDF disguised as text"),
        ("manual.md", b"%PDF-1.7\nPDF disguised as Markdown"),
        ("manual.markdown", b"%PDF-1.7\nPDF disguised as Markdown"),
        ("manual.txt", b"\x00binary"),
        ("manual.md", b"\xffinvalid utf8"),
    ],
)
def test_upload_rejects_unsupported_or_mismatched_content(
    filename: str,
    content: bytes,
) -> None:
    with pytest.raises(UnsupportedDocumentTypeError):
        stage_document_upload(filename=filename, source=BytesIO(content))


def test_upload_enforces_streamed_limit_without_trusting_headers() -> None:
    exact = stage_document_upload(
        filename="exact.txt",
        source=BytesIO(b"x" * 10),
        max_bytes=10,
    )
    exact.close()

    with pytest.raises(DocumentTooLargeError):
        stage_document_upload(
            filename="large.txt",
            source=BytesIO(b"x" * 11),
            max_bytes=10,
        )


def test_upload_rejects_empty_content() -> None:
    with pytest.raises(EmptyDocumentError):
        stage_document_upload(filename="empty.txt", source=BytesIO())
