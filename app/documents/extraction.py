from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO, Protocol

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.documents.models import DocumentErrorCode, DocumentMediaType


class PermanentExtractionError(RuntimeError):
    """A deterministic document failure that must not be retried."""

    def __init__(self, error_code: DocumentErrorCode) -> None:
        self.error_code = error_code
        super().__init__(error_code.value)


class DocumentExtractor(Protocol):
    def extract(self, source: BinaryIO, limits: ExtractionLimits) -> str: ...


@dataclass(frozen=True)
class ExtractionLimits:
    max_pdf_pages: int
    max_characters: int


class TextDocumentExtractor:
    """Strict UTF-8 extraction shared by plain text and Markdown."""

    def extract(self, source: BinaryIO, limits: ExtractionLimits) -> str:
        try:
            content = source.read()
            if b"\x00" in content:
                raise UnicodeDecodeError("utf-8", content, 0, 1, "NUL byte")
            text = content.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise PermanentExtractionError(
                DocumentErrorCode.UNSUPPORTED_DOCUMENT
            ) from exc
        if len(text) > limits.max_characters:
            raise PermanentExtractionError(DocumentErrorCode.EXTRACTION_LIMIT_EXCEEDED)
        return text


class PdfDocumentExtractor:
    """Bounded, non-OCR PDF text extraction using the BSD-licensed pypdf."""

    def extract(self, source: BinaryIO, limits: ExtractionLimits) -> str:
        try:
            reader = PdfReader(source, strict=True)
            if reader.is_encrypted:
                raise PermanentExtractionError(DocumentErrorCode.EXTRACTION_FAILED)
            if len(reader.pages) > limits.max_pdf_pages:
                raise PermanentExtractionError(
                    DocumentErrorCode.EXTRACTION_LIMIT_EXCEEDED
                )

            parts: list[str] = []
            character_count = 0
            for page in reader.pages:
                page_text = page.extract_text() or ""
                character_count += len(page_text)
                if parts:
                    character_count += 1
                if character_count > limits.max_characters:
                    raise PermanentExtractionError(
                        DocumentErrorCode.EXTRACTION_LIMIT_EXCEEDED
                    )
                parts.append(page_text)
            return "\n".join(parts)
        except PermanentExtractionError:
            raise
        except (PdfReadError, OSError, ValueError) as exc:
            raise PermanentExtractionError(DocumentErrorCode.EXTRACTION_FAILED) from exc


class DocumentExtractorRegistry:
    def __init__(self) -> None:
        text = TextDocumentExtractor()
        self._extractors: dict[DocumentMediaType, DocumentExtractor] = {
            DocumentMediaType.PDF: PdfDocumentExtractor(),
            DocumentMediaType.TEXT: text,
            DocumentMediaType.MARKDOWN: text,
        }

    def get(self, media_type: DocumentMediaType) -> DocumentExtractor:
        try:
            return self._extractors[media_type]
        except KeyError as exc:
            raise PermanentExtractionError(
                DocumentErrorCode.UNSUPPORTED_DOCUMENT
            ) from exc
