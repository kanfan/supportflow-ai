from io import BytesIO

import pytest
from pypdf import PdfWriter

from app.documents.extraction import (
    DocumentExtractorRegistry,
    ExtractionLimits,
    PermanentExtractionError,
)
from app.documents.models import DocumentErrorCode, DocumentMediaType


def build_text_pdf(text: str) -> bytes:
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        (
            b"<< /Length "
            + str(len(content)).encode()
            + b" >>\nstream\n"
            + content
            + b"\nendstream"
        ),
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode())
        output.extend(body)
        output.extend(b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode()
    )
    return bytes(output)


def limits(*, pages: int = 250, characters: int = 2_000_000) -> ExtractionLimits:
    return ExtractionLimits(max_pdf_pages=pages, max_characters=characters)


@pytest.mark.parametrize(
    "media_type",
    [DocumentMediaType.TEXT, DocumentMediaType.MARKDOWN],
)
def test_text_extractors_return_strict_utf8_without_execution(
    media_type: DocumentMediaType,
) -> None:
    content = "# Support\n<script>never executed</script>\nTürkçe"

    extracted = (
        DocumentExtractorRegistry()
        .get(media_type)
        .extract(
            BytesIO(content.encode()),
            limits(),
        )
    )

    assert extracted == content


def test_pdf_extractor_returns_deterministic_plain_text() -> None:
    extracted = (
        DocumentExtractorRegistry()
        .get(DocumentMediaType.PDF)
        .extract(
            BytesIO(build_text_pdf("Hello SupportFlow")),
            limits(),
        )
    )

    assert extracted == "Hello SupportFlow"


@pytest.mark.parametrize(
    ("content", "expected_code"),
    [
        (b"not a valid PDF", DocumentErrorCode.EXTRACTION_FAILED),
        (b"valid\x00binary", DocumentErrorCode.UNSUPPORTED_DOCUMENT),
        (b"\xff\xfe", DocumentErrorCode.UNSUPPORTED_DOCUMENT),
    ],
)
def test_corrupt_pdf_and_invalid_text_fail_permanently(
    content: bytes,
    expected_code: DocumentErrorCode,
) -> None:
    media_type = (
        DocumentMediaType.PDF
        if content == b"not a valid PDF"
        else DocumentMediaType.TEXT
    )

    with pytest.raises(PermanentExtractionError) as captured:
        DocumentExtractorRegistry().get(media_type).extract(
            BytesIO(content),
            limits(),
        )

    assert captured.value.error_code is expected_code


def test_pdf_page_and_text_character_limits_fail_safely() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_blank_page(width=100, height=100)
    two_page_pdf = BytesIO()
    writer.write(two_page_pdf)

    with pytest.raises(PermanentExtractionError) as page_error:
        DocumentExtractorRegistry().get(DocumentMediaType.PDF).extract(
            BytesIO(two_page_pdf.getvalue()),
            limits(pages=1),
        )
    with pytest.raises(PermanentExtractionError) as character_error:
        DocumentExtractorRegistry().get(DocumentMediaType.TEXT).extract(
            BytesIO(b"four"),
            limits(characters=3),
        )

    assert page_error.value.error_code is DocumentErrorCode.EXTRACTION_LIMIT_EXCEEDED
    assert (
        character_error.value.error_code is DocumentErrorCode.EXTRACTION_LIMIT_EXCEEDED
    )


def test_encrypted_pdf_fails_without_attempting_password_handling() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.encrypt("private-password")
    encrypted = BytesIO()
    writer.write(encrypted)

    with pytest.raises(PermanentExtractionError) as captured:
        DocumentExtractorRegistry().get(DocumentMediaType.PDF).extract(
            BytesIO(encrypted.getvalue()),
            limits(),
        )

    assert captured.value.error_code is DocumentErrorCode.EXTRACTION_FAILED
