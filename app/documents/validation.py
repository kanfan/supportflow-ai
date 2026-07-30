from __future__ import annotations

from dataclasses import dataclass
import hashlib
from tempfile import SpooledTemporaryFile
from typing import BinaryIO, cast
import unicodedata

from app.documents.models import DocumentMediaType, MAX_DOCUMENT_BYTES


READ_CHUNK_BYTES = 64 * 1024
DISPLAY_FILENAME_MAX_CHARS = 200
SPOOL_MEMORY_BYTES = 1024 * 1024

MEDIA_TYPE_BY_EXTENSION = {
    ".pdf": DocumentMediaType.PDF,
    ".txt": DocumentMediaType.TEXT,
    ".md": DocumentMediaType.MARKDOWN,
    ".markdown": DocumentMediaType.MARKDOWN,
}


class DocumentValidationError(ValueError):
    code = "invalid_document"
    user_message = "The uploaded document is invalid"


class DocumentTooLargeError(DocumentValidationError):
    code = "document_too_large"
    user_message = "The uploaded document exceeds the 10 MiB limit"


class UnsupportedDocumentTypeError(DocumentValidationError):
    code = "unsupported_document_type"
    user_message = "Only PDF, TXT, and Markdown documents are supported"


class UnsafeDocumentFilenameError(DocumentValidationError):
    code = "unsafe_document_filename"
    user_message = "The document filename is invalid"


class EmptyDocumentError(DocumentValidationError):
    code = "empty_document"
    user_message = "The uploaded document is empty"


@dataclass
class ValidatedDocumentUpload:
    display_filename: str
    media_type: DocumentMediaType
    size_bytes: int
    content_sha256: str
    stream: BinaryIO

    def close(self) -> None:
        self.stream.close()


def normalize_display_filename(filename: str | None) -> tuple[str, str]:
    if filename is None:
        raise UnsafeDocumentFilenameError

    normalized = unicodedata.normalize("NFKC", filename).strip()
    if (
        not normalized
        or normalized in {".", ".."}
        or "/" in normalized
        or "\\" in normalized
        or any(
            unicodedata.category(character).startswith("C") for character in normalized
        )
        or len(normalized) > DISPLAY_FILENAME_MAX_CHARS
    ):
        raise UnsafeDocumentFilenameError

    dot_index = normalized.rfind(".")
    if dot_index <= 0:
        raise UnsupportedDocumentTypeError
    extension = normalized[dot_index:].lower()
    if extension not in MEDIA_TYPE_BY_EXTENSION:
        raise UnsupportedDocumentTypeError
    return f"{normalized[:dot_index]}{extension}", extension


def _validate_content(media_type: DocumentMediaType, content: BinaryIO) -> None:
    content.seek(0)
    prefix = content.read(5)
    content.seek(0)
    if media_type is DocumentMediaType.PDF:
        if prefix != b"%PDF-":
            raise UnsupportedDocumentTypeError
        return

    payload = content.read()
    content.seek(0)
    if b"\x00" in payload:
        raise UnsupportedDocumentTypeError
    try:
        payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise UnsupportedDocumentTypeError from exc


def stage_document_upload(
    *,
    filename: str | None,
    source: BinaryIO,
    max_bytes: int = MAX_DOCUMENT_BYTES,
) -> ValidatedDocumentUpload:
    display_filename, extension = normalize_display_filename(filename)
    media_type = MEDIA_TYPE_BY_EXTENSION[extension]
    staged = cast(
        BinaryIO,
        SpooledTemporaryFile(max_size=SPOOL_MEMORY_BYTES, mode="w+b"),
    )
    digest = hashlib.sha256()
    size_bytes = 0

    try:
        source.seek(0)
        while chunk := source.read(READ_CHUNK_BYTES):
            size_bytes += len(chunk)
            if size_bytes > max_bytes:
                raise DocumentTooLargeError
            digest.update(chunk)
            staged.write(chunk)

        if size_bytes == 0:
            raise EmptyDocumentError

        staged.seek(0)
        _validate_content(media_type, staged)
        staged.seek(0)
        return ValidatedDocumentUpload(
            display_filename=display_filename,
            media_type=media_type,
            size_bytes=size_bytes,
            content_sha256=digest.hexdigest(),
            stream=staged,
        )
    except Exception:
        staged.close()
        raise
