from pathlib import Path

import pytest
from reportlab.pdfgen import canvas

from scripts import revise_project_plan_for_aws as project_plan


def create_source_pdf(
    path: Path,
    *,
    pages: int = project_plan.EXPECTED_SOURCE_PAGE_COUNT,
    title: str = project_plan.EXPECTED_SOURCE_TITLE,
    revision_marker: str = project_plan.EXPECTED_SOURCE_REVISION_MARKER,
) -> None:
    document = canvas.Canvas(str(path))
    document.setTitle(title)
    for page_number in range(pages):
        if page_number == 0:
            document.drawString(72, 720, revision_marker)
        document.drawString(72, 700, f"Test page {page_number + 1}")
        document.showPage()
    document.save()


def test_source_contract_accepts_revision_1_2_layout(tmp_path: Path) -> None:
    source = tmp_path / "revision-1.2.pdf"
    create_source_pdf(source)

    reader = project_plan.validate_source_pdf(source)

    assert len(reader.pages) == project_plan.EXPECTED_SOURCE_PAGE_COUNT
    assert reader.metadata is not None
    assert reader.metadata.title == project_plan.EXPECTED_SOURCE_TITLE


def test_source_contract_rejects_23_page_original_before_redaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "revision-1.1.pdf"
    output = tmp_path / "revision-1.3.pdf"
    create_source_pdf(source, pages=23)
    redaction_called = False

    def unexpected_redaction(_: Path) -> None:
        nonlocal redaction_called
        redaction_called = True

    monkeypatch.setattr(project_plan, "redact_original_content", unexpected_redaction)

    with pytest.raises(ValueError, match=r"page count 23.*expected 29"):
        project_plan.revise_pdf(source, output)

    assert redaction_called is False
    assert output.exists() is False


@pytest.mark.parametrize(
    ("title", "revision_marker", "expected_error"),
    [
        (
            "SupportFlow AI - Uçtan Uca Proje Planı (AWS Revision 1.1)",
            project_plan.EXPECTED_SOURCE_REVISION_MARKER,
            "title",
        ),
        (
            project_plan.EXPECTED_SOURCE_TITLE,
            "Rapor sürümü: 1.1",
            "first-page revision marker",
        ),
    ],
)
def test_source_contract_rejects_wrong_title_or_revision(
    tmp_path: Path,
    title: str,
    revision_marker: str,
    expected_error: str,
) -> None:
    source = tmp_path / "wrong-source.pdf"
    create_source_pdf(
        source,
        title=title,
        revision_marker=revision_marker,
    )

    with pytest.raises(ValueError, match=expected_error) as captured:
        project_plan.validate_source_pdf(source)

    message = str(captured.value)
    assert project_plan.EXPECTED_SOURCE_COMMIT in message
    assert project_plan.EXPECTED_SOURCE_REVISION in message


def test_source_contract_reports_missing_file_with_recovery_details(
    tmp_path: Path,
) -> None:
    source = tmp_path / "missing.pdf"

    with pytest.raises(ValueError, match="not a readable file") as captured:
        project_plan.validate_source_pdf(source)

    assert project_plan.EXPECTED_SOURCE_COMMIT in str(captured.value)
