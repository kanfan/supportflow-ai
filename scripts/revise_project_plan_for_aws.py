"""Create Revision 1.3 from the 29-page AWS Revision 1.2 project plan.

The source contract is intentionally strict because every redaction coordinate
is tied to Revision 1.2's 29-page layout. Recover the expected source from Git
commit ``4b869d2`` as documented in the README. The script must not replace
unrelated language such as "server-rendered UI".

Usage:
    uv run --with PyMuPDF python scripts/revise_project_plan_for_aws.py \
        SOURCE.pdf OUTPUT.pdf
"""

from __future__ import annotations

import argparse
from io import BytesIO
from pathlib import Path
from collections.abc import Sequence
from typing import Any

from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph


PAGE_WIDTH = 612
PAGE_HEIGHT = 792
EXPECTED_SOURCE_COMMIT = "4b869d2"
EXPECTED_SOURCE_PAGE_COUNT = 29
EXPECTED_SOURCE_REVISION = "1.2"
EXPECTED_SOURCE_TITLE = "SupportFlow AI - Uçtan Uca Proje Planı (AWS Revision 1.2)"
EXPECTED_SOURCE_REVISION_MARKER = "Rapor sürümü: 1.2"
BLUE = colors.HexColor("#1F4E79")
ACCENT_BLUE = colors.HexColor("#2E75B6")
LIGHT_BLUE = colors.HexColor("#EAF2F8")
LIGHT_GREEN = colors.HexColor("#E2F0D9")
LIGHT_GRAY = colors.HexColor("#F2F4F7")
LIGHT_YELLOW = colors.HexColor("#FFF2CC")
BLACK = colors.black
WHITE = colors.white


def register_fonts() -> None:
    font_dir = Path("C:/Windows/Fonts")
    pdfmetrics.registerFont(TTFont("Arial", font_dir / "arial.ttf"))
    pdfmetrics.registerFont(TTFont("Arial-Bold", font_dir / "arialbd.ttf"))
    pdfmetrics.registerFont(TTFont("Arial-Italic", font_dir / "ariali.ttf"))


def y_from_top(top: float, height: float = 0) -> float:
    return PAGE_HEIGHT - top - height


def whiteout(
    target: canvas.Canvas,
    x: float,
    top: float,
    width: float,
    height: float,
) -> None:
    target.setFillColor(WHITE)
    target.setStrokeColor(WHITE)
    target.rect(x, y_from_top(top, height), width, height, fill=1, stroke=0)


def fill_top(
    target: canvas.Canvas,
    x: float,
    top: float,
    width: float,
    height: float,
    color: colors.Color,
) -> None:
    target.setFillColor(color)
    target.setStrokeColor(color)
    target.rect(x, y_from_top(top, height), width, height, fill=1, stroke=0)


def text_top(
    target: canvas.Canvas,
    x: float,
    top: float,
    text: str,
    *,
    font: str = "Arial",
    size: float = 10.5,
    color: colors.Color = BLACK,
) -> None:
    target.setFillColor(color)
    target.setFont(font, size)
    target.drawString(x, PAGE_HEIGHT - top - size, text)


def centered_text_top(
    target: canvas.Canvas,
    top: float,
    text: str,
    *,
    font: str = "Arial",
    size: float = 10.5,
    color: colors.Color = BLACK,
) -> None:
    target.setFillColor(color)
    target.setFont(font, size)
    target.drawCentredString(PAGE_WIDTH / 2, PAGE_HEIGHT - top - size, text)


def paragraph_top(
    target: canvas.Canvas,
    x: float,
    top: float,
    width: float,
    text: str,
    *,
    font: str = "Arial",
    size: float = 10.5,
    leading: float = 12.5,
    color: colors.Color = BLACK,
    alignment: Any = TA_LEFT,
) -> float:
    style = ParagraphStyle(
        name="overlay",
        fontName=font,
        fontSize=size,
        leading=leading,
        textColor=color,
        alignment=alignment,
        spaceAfter=0,
        spaceBefore=0,
    )
    paragraph = Paragraph(text, style)
    _, height = paragraph.wrap(width, PAGE_HEIGHT)
    paragraph.drawOn(target, x, PAGE_HEIGHT - top - height)
    return height


def bullet_lines(
    target: canvas.Canvas,
    top: float,
    items: list[str],
    *,
    x: float = 54,
    width: float = 504,
    size: float = 9.3,
    leading: float = 11.3,
    gap: float = 1.0,
) -> float:
    cursor = top
    for item in items:
        height = paragraph_top(
            target,
            x,
            cursor,
            width,
            f"•&nbsp;&nbsp;{item}",
            size=size,
            leading=leading,
        )
        cursor += height + gap
    return cursor


def draw_grid(
    target: canvas.Canvas,
    x_positions: Sequence[float],
    top_positions: Sequence[float],
) -> None:
    target.setStrokeColor(BLACK)
    target.setLineWidth(0.5)
    left, right = x_positions[0], x_positions[-1]
    top, bottom = top_positions[0], top_positions[-1]
    for x in x_positions:
        target.line(x, y_from_top(top), x, y_from_top(bottom))
    for row_top in top_positions:
        target.line(left, y_from_top(row_top), right, y_from_top(row_top))


def draw_page_1(target: canvas.Canvas) -> None:
    whiteout(target, 78, 296, 456, 74)
    centered_text_top(target, 314, "Plan süresi: 12 hafta", size=11)
    centered_text_top(target, 329, "Mimari: Modüler monolith", size=11)
    centered_text_top(
        target,
        344,
        "Yayın hedefi: AWS'te kısa ömürlü, production-shaped portföy kanıtı",
        size=10.1,
    )
    centered_text_top(
        target,
        359,
        "Rapor sürümü: 1.3 - AWS portföy kanıtı ve 24 saat teardown revizyonu",
        size=9.8,
    )

    whiteout(target, 52, 427, 508, 42)
    centered_text_top(
        target,
        429,
        "Hazırlanma amacı: Fikirden tekrar üretilebilir ürüne, teknik sunuma ve",
        font="Arial-Italic",
        size=9.7,
    )
    centered_text_top(
        target,
        443,
        "sentetik verili AWS portföy kanıtına kadar çalışma düzenini tanımlamak.",
        font="Arial-Italic",
        size=9.7,
    )
    centered_text_top(
        target,
        457,
        "Son revizyon: 13 Ağustos 2026.",
        font="Arial-Italic",
        size=9.7,
    )


def draw_page_2(target: canvas.Canvas) -> None:
    whiteout(target, 53, 267, 330, 18)
    text_top(
        target,
        54,
        269.5,
        "14. AWS yayınlama ve sürüm yönetimi",
        size=10.5,
    )


def draw_page_3(target: canvas.Canvas) -> None:
    row_top, row_bottom = 288.55, 309.05
    whiteout(target, 54, row_top, 504, row_bottom - row_top)
    fill_top(target, 54, row_top, 504, row_bottom - row_top, WHITE)
    text_top(target, 59.5, 294, "Yayın", size=8)
    text_top(target, 227.5, 294, "AWS (ECS/Fargate)", size=8)
    text_top(
        target,
        395.5,
        290.5,
        "API/worker, RDS, ElastiCache ve S3",
        size=7.6,
    )
    text_top(
        target,
        395.5,
        299.5,
        "yönetilen servislerle ayrıştırılır.",
        size=7.6,
    )
    draw_grid(target, [54, 222, 390, 558], [row_top, row_bottom])

    whiteout(target, 54, 353, 504, 49)
    fill_top(target, 54, 353, 504, 49, LIGHT_BLUE)
    paragraph_top(
        target,
        59.5,
        357,
        493,
        (
            "<b>Tek cümlelik hedef:</b> 12. haftanın sonunda kullanıcı girişi olan, "
            "tenant izolasyonu sağlayan, doküman yükleyen, ticket oluşturan, RAG ile "
            "kaynaklı cevap öneren, insan onayı toplayan ve test edilen ürün v1.0’ı "
            "planlı, kısa ömürlü bir AWS kanıt penceresinde doğrulamak; kanıtları "
            "saklayıp kaynakları 24 saat içinde destroy etmek."
        ),
        size=9.2,
        leading=11.0,
    )

    fill_top(target, 54, 410, 504, 49, LIGHT_YELLOW)
    paragraph_top(
        target,
        59.5,
        414,
        493,
        (
            "<b>İki ayrı hazır olma seviyesi:</b> Portföy-ready: sentetik veriyle "
            "tekrar üretilebilir AWS apply/deploy/verify/destroy kanıtı. Pilot-ready: "
            "doğrulanmış hedef müşteri, gerçek kanal ve veri işleme/aktarım kapıları; "
            "bu karar AWS ortamını sürekli açık tutmaz."
        ),
        size=8.8,
        leading=10.5,
    )


def draw_page_4(target: canvas.Canvas) -> None:
    top, bottom = 434.45, 456.25
    whiteout(target, 390, top, 168, bottom - top)
    fill_top(target, 390, top, 168, bottom - top, WHITE)
    text_top(target, 395.5, 436, "ALB/ECS health check +", size=8.1)
    text_top(target, 395.5, 445.5, "uygulama metrikleri", size=8.1)
    draw_grid(target, [390, 558], [top, bottom])


def draw_page_5(target: canvas.Canvas) -> None:
    top, bottom = 105.55, 127.35
    whiteout(target, 306, top, 252, bottom - top)
    fill_top(target, 306, top, 252, bottom - top, WHITE)
    text_top(
        target,
        311.5,
        107,
        "Docker, GitHub Actions, ECR/ECS/ALB + RDS +",
        size=7.8,
    )
    text_top(
        target,
        311.5,
        116,
        "ElastiCache + S3, seed demo organization",
        size=7.8,
    )
    draw_grid(target, [306, 558], [top, bottom])

    top, bottom = 652.05, 673.85
    whiteout(target, 222, top, 168, bottom - top)
    fill_top(target, 222, top, 168, bottom - top, LIGHT_GRAY)
    text_top(
        target,
        227.5,
        654,
        "AWS, env vars, migration, smoke test",
        size=7.7,
    )
    text_top(
        target,
        227.5,
        663,
        "ve rollback adımlarını yürütür.",
        size=7.7,
    )
    draw_grid(target, [222, 390], [top, bottom])


def draw_page_7(target: canvas.Canvas) -> None:
    row_positions = [80.35, 102.15, 123.95, 145.75, 167.55, 189.35, 211.15]
    whiteout(target, 390, row_positions[0], 168, row_positions[-1] - row_positions[0])

    values = [
        ("ECS/Fargate + ALB /", "local Docker"),
        ("ECS/Fargate service /", "local Docker"),
        ("RDS PostgreSQL + pgvector /", "local container"),
        ("ElastiCache Valkey/Redis /", "local container"),
        ("Amazon S3 (private, encrypted,", "versioned)"),
        ("Harici API; testlerde", "fake adapter"),
    ]
    for index, (line_1, line_2) in enumerate(values):
        top = row_positions[index]
        bottom = row_positions[index + 1]
        fill_top(
            target,
            390,
            top,
            168,
            bottom - top,
            WHITE if index % 2 == 0 else LIGHT_GRAY,
        )
        text_top(target, 395.5, top + 2, line_1, size=7.5)
        text_top(target, 395.5, top + 10.8, line_2, size=7.5)
    draw_grid(target, [390, 558], row_positions)


def draw_page_14(target: canvas.Canvas) -> None:
    whiteout(target, 53, 168, 505, 346)

    text_top(
        target,
        54,
        174,
        "Hafta 5 - AWS portföy kanıtı ve uygulama entegrasyonu",
        font="Arial-Bold",
        size=14,
        color=ACCENT_BLUE,
    )
    fill_top(target, 54, 195, 504, 22, LIGHT_BLUE)
    paragraph_top(
        target,
        59,
        198,
        494,
        (
            "<b>Haftanın amacı:</b> Backend’i sentetik veriyle kısa ömürlü, güvenli, "
            "geri alınabilir ve tekrar üretilebilir bir AWS kanıt penceresinde doğrulamak."
        ),
        size=9.2,
        leading=10.5,
    )

    text_top(target, 54, 225, "Emir’in görevleri", font="Arial-Bold", size=10)
    cursor = bullet_lines(
        target,
        241,
        [
            "S3/scanner/session/readiness ve TLS uygulama sınırlarını geliştirip test eder.",
            "Terraform planı, IAM, ağ, maliyet, secret ve handoff sözleşmelerini review eder.",
            "Sentetik teknik kabul matrisini, güvenlik negatiflerini ve kanıt paketini yönetir.",
        ],
        size=8.8,
        leading=10.5,
    )

    text_top(target, 54, cursor + 1, "Eray’ın görevleri", font="Arial-Bold", size=10)
    cursor = bullet_lines(
        target,
        cursor + 17,
        [
            "AWS account/billing, Terraform state, VPC/IAM/ECS/RDS/Redis/S3 temelini kurar.",
            "GitHub OIDC ile tek digest migration, API ve worker deployment akışını geliştirir.",
            "USD 10/25/50 uyarıları, USD 120 emergency ceiling ve teardown runbook’unu işletir.",
        ],
        size=8.8,
        leading=10.5,
    )

    text_top(target, 54, cursor + 1, "Ortak yapılacaklar", font="Arial-Bold", size=10)
    cursor = bullet_lines(
        target,
        cursor + 17,
        [
            "Yalnız ALB public; ECS private, RDS/ElastiCache izole ve S3 private çalışır.",
            "ClamAV sidecar, TLS, readiness, tenant sınırları ve secret redaction doğrulanır.",
            "Rollback, RDS restore, S3 recovery, alarm ve cost-response prosedürü prova edilir.",
            "Arındırılmış kanıt/video alınır; disposable kaynaklar 24 saat içinde destroy edilir.",
        ],
        size=8.8,
        leading=10.5,
    )

    text_top(target, 54, cursor + 1, "Kabul kriterleri", font="Arial-Bold", size=10)
    cursor = bullet_lines(
        target,
        cursor + 17,
        [
            "Login/ticket/upload/worker smoke akışı sentetik veride geçer.",
            "Alembic one-off task, API ve worker aynı immutable digest’i kullanır.",
            "OIDC, observability, rollback/restore ve cost kanıtları kayıt altındadır.",
            "Kalıcı URL/key yoktur; resource inventory boşluğu ve destroy sonucu doğrulanır.",
        ],
        size=8.8,
        leading=10.5,
    )

    fill_top(target, 54, cursor + 2, 504, 16, LIGHT_GREEN)
    paragraph_top(
        target,
        59,
        cursor + 4,
        494,
        "<b>Haftalık sahiplik:</b> Platform/Release Captain: Eray | App Verification Lead: Emir",
        size=8.8,
        leading=10,
    )


def draw_page_17(target: canvas.Canvas) -> None:
    whiteout(target, 54, 395, 504, 16)
    paragraph_top(
        target,
        54,
        397,
        504,
        "•&nbsp;&nbsp;RC, planlı AWS kanıt penceresinde doğrulanır; kanıt sonrası ortam destroy edilir.",
        size=9.4,
        leading=11,
    )

    whiteout(target, 54, 539, 504, 16)
    paragraph_top(
        target,
        59.5,
        541,
        494,
        (
            "<b>Haftanın amacı:</b> v1.0 kanıt paketi, tekrar üretilebilir demo, "
            "güçlü README ve mülakat anlatımı."
        ),
        size=8.9,
        leading=10.5,
    )

    whiteout(target, 54, 642, 504, 16)
    paragraph_top(
        target,
        54,
        643,
        504,
        (
            "•&nbsp;&nbsp;v1.0 tag, OIDC/ECS evidence window, migration, rollback ve "
            "24 saat teardown kontrolünü yönetir."
        ),
        size=9.3,
        leading=11,
    )

    whiteout(target, 54, 702, 504, 16)
    paragraph_top(
        target,
        54,
        704,
        504,
        "•&nbsp;&nbsp;Kalıcı servis yerine arındırılmış v1.0 AWS kanıt paketi yayımlanır.",
        size=9.5,
        leading=11,
    )


def draw_page_20(target: canvas.Canvas) -> None:
    whiteout(target, 52, 312, 509, 416)

    text_top(
        target,
        54,
        323,
        "14. AWS yayınlama ve sürüm yönetimi",
        font="Arial-Bold",
        size=18,
        color=BLUE,
    )

    text_top(
        target,
        54,
        357,
        "14.1 Ortamlar ve veri sınırı",
        font="Arial-Bold",
        size=14,
        color=ACCENT_BLUE,
    )
    table_top = 380
    row_positions = [table_top, 394, 418, 442, 466]
    columns = [54, 222, 390, 558]
    fill_top(target, 54, row_positions[0], 504, 14, BLUE)
    for index in range(1, 4):
        fill_top(
            target,
            54,
            row_positions[index],
            504,
            row_positions[index + 1] - row_positions[index],
            WHITE if index % 2 else LIGHT_GRAY,
        )
    text_top(target, 59, 381, "Ortam", font="Arial-Bold", size=8, color=WHITE)
    text_top(target, 227, 381, "Amaç", font="Arial-Bold", size=8, color=WHITE)
    text_top(
        target, 395, 381, "Veri ve AWS sınırı", font="Arial-Bold", size=8, color=WHITE
    )

    rows = [
        ("Local", "Geliştirme/debug", "Docker Compose; fake/seed data"),
        (
            "Local Validation",
            "Ürün/usability doğrulama",
            "Sentetik fixture; AWS beklemez",
        ),
        ("AWS Evidence", "Deploy/recovery kanıtı", "Sentetik; <=24 saatte destroy"),
    ]
    for index, row in enumerate(rows, start=1):
        top = row_positions[index] + 2
        text_top(target, 59, top, row[0], size=7.7)
        paragraph_top(target, 227, top, 158, row[1], size=7.7, leading=8.8)
        paragraph_top(target, 395, top, 157, row[2], size=7.7, leading=8.8)
    draw_grid(target, columns, row_positions)

    text_top(
        target,
        54,
        479,
        "14.2 AWS servisleri ve güvenlik sınırı",
        font="Arial-Bold",
        size=14,
        color=ACCENT_BLUE,
    )
    cursor = bullet_lines(
        target,
        500,
        [
            "ECR’da immutable commit-SHA image; ECS/Fargate API ve ayrı Celery worker.",
            "Public ALB + ACM; ECS, RDS PostgreSQL+pgvector ve ElastiCache private subnetlerde.",
            "Private, encrypted, versioned S3; Secrets Manager; CloudWatch/CloudTrail/Budgets.",
            "Tek kısa ömürlü evidence environment; GitHub Actions OIDC ile geçici kimlik.",
            "ClamAV worker sidecar; USD 10/25/50 uyarıları, USD 120 emergency ceiling.",
        ],
        size=8.7,
        leading=10.3,
    )

    text_top(
        target,
        54,
        cursor + 4,
        "14.3 Deploy, migration ve rollback sırası",
        font="Arial-Bold",
        size=14,
        color=ACCENT_BLUE,
    )
    cursor += 25
    steps = [
        "CI kalite/test/image kontrolleri geçer; image ECR’a SHA/digest ile push edilir.",
        "Backup/rollback riski değerlendirilir; Alembic one-off ECS task olarak çalışır.",
        "API ve worker aynı image digest ile deploy edilir; readiness gate beklenir.",
        "Login, ticket, document, worker ve AI smoke akışları çalıştırılır.",
        "Uygulama hatasında önceki ECS revision’a dönülür; migration için forward-fix esastır.",
        "Veri olayı varsa doğrulanmış RDS point-in-time restore prosedürü kullanılır.",
        "Arındırılmış kanıt alınır; disposable AWS kaynakları 24 saat içinde destroy ve verify edilir.",
    ]
    for number, step in enumerate(steps, start=1):
        height = paragraph_top(
            target,
            54,
            cursor,
            504,
            f"{number}.&nbsp;&nbsp;{step}",
            size=8.3,
            leading=9.8,
        )
        cursor += height + 1


def draw_page_21(target: canvas.Canvas) -> None:
    whiteout(target, 53, 49, 506, 120)
    text_top(
        target,
        54,
        50,
        "14.4 Sürüm kilometre taşları",
        font="Arial-Bold",
        size=14,
        color=ACCENT_BLUE,
    )
    rows = [
        ("Sürüm", "Hafta", "Kanıt çıktısı"),
        (
            "v0.1 Alpha",
            "5",
            "AWS foundation + deploy/rollback/restore/destroy evidence",
        ),
        ("v0.5 Beta", "9", "AI/RAG beta; gerekirse yeni kısa AWS evidence window"),
        ("v0.9 RC", "11", "Hardening ve release kanıt provası; kalıcı servis yok"),
        ("v1.0", "12", "Arındırılmış kanıt, README, video ve portföy paketi"),
    ]
    columns = [54, 150, 198, 558]
    positions = [69, 84, 105, 126, 147, 168]
    for index in range(len(rows)):
        fill_top(
            target,
            54,
            positions[index],
            504,
            positions[index + 1] - positions[index],
            BLUE if index == 0 else (WHITE if index % 2 else LIGHT_GRAY),
        )
        color = WHITE if index == 0 else BLACK
        font = "Arial-Bold" if index == 0 else "Arial"
        top = positions[index] + 3
        text_top(target, 59, top, rows[index][0], font=font, size=7.4, color=color)
        text_top(target, 155, top, rows[index][1], font=font, size=7.4, color=color)
        paragraph_top(
            target,
            203,
            top,
            349,
            rows[index][2],
            font=font,
            size=7.4,
            leading=8.5,
            color=color,
        )
    draw_grid(target, columns, positions)


def draw_page_23(target: canvas.Canvas) -> None:
    whiteout(target, 53, 49, 506, 178)
    text_top(
        target,
        54,
        50,
        "15.8 README ve portföy bölümleri",
        font="Arial-Bold",
        size=14,
        color=ACCENT_BLUE,
    )
    bullet_lines(
        target,
        72,
        [
            "Problem, hedef niş ve ürün değeri",
            "Pazar örnekleri ve SupportFlow’un farklılaşması",
            "Arındırılmış AWS kanıtı, demo videosu ve local yeniden üretim adımları",
            "Mimari diagram ve request flow",
            "Stack ve nedenleri",
            "Local setup ve environment variables",
            "Database/tenant modeli ve connector adapter yaklaşımı",
            "LLM/RAG pipeline, Türkçe evaluation ve no-answer yaklaşımı",
            "Test/CI/CD ve deployment",
            "Security, KVKK yayın kapısı ve bilinen sınırlamalar",
            "Pilot metrikleri, trade-off’lar ve v1.1 roadmap",
            "Emir ve Eray’ın katkı özeti; ikisinin de AI/RAG katkıları",
        ],
        size=8.6,
        leading=9.7,
        gap=0,
    )

    top, bottom = 390.65, 410.05
    whiteout(target, 54, top, 504, bottom - top)
    fill_top(target, 54, top, 504, bottom - top, WHITE)
    text_top(target, 59, top + 1, "AWS/harici servis", size=7.1)
    text_top(target, 59, top + 9, "sorunu", size=7.1)
    text_top(target, 185, top + 5, "Düşük/Orta", size=7.1)
    text_top(target, 311, top + 1, "Deploy, bölge veya", size=7.1)
    text_top(target, 311, top + 9, "kota problemi", size=7.1)
    text_top(target, 437, top + 1, "IaC, local Docker, 24h destroy,", size=6.2)
    text_top(target, 437, top + 9, "USD 10/25/50 uyarıları", size=6.2)
    draw_grid(target, [54, 180, 306, 432, 558], [top, bottom])


def draw_page_24(target: canvas.Canvas) -> None:
    whiteout(target, 53, 337, 506, 18)
    target.setFillColor(WHITE)
    target.setStrokeColor(colors.HexColor("#666666"))
    target.setLineWidth(0.6)
    target.rect(54, y_from_top(341, 7), 7, 7, fill=0, stroke=1)
    text_top(
        target,
        66,
        340,
        "Kalıcı demo hesabı yerine arındırılmış AWS kanıtı ve local demo adımları doğrulandı.",
        size=8.8,
    )


def draw_page_27(target: canvas.Canvas) -> None:
    whiteout(target, 53, 49, 506, 132)
    text_top(
        target,
        54,
        50,
        "Ek E - Nihai görev matrisi özeti",
        font="Arial-Bold",
        size=16,
        color=BLUE,
    )
    rows = [
        ("Çıktı", "Emir", "Eray", "Ortak entegrasyon"),
        (
            "Identity/tenant",
            "Ana implementasyon",
            "Security test/review",
            "Threat model",
        ),
        ("Ticket", "Review + approval", "Ana implementasyon", "E2E akış"),
        ("Celery", "Idempotency hardening", "Temel altyapı", "Failure test"),
        ("LLM", "Client/structured output", "Classification/prompt", "İlk çağrı pair"),
        (
            "RAG ingestion",
            "Chunking/version",
            "Embedding/vector",
            "Parametre deneyleri",
        ),
        (
            "RAG answer",
            "Retrieval/citation",
            "Generation/no-answer",
            "Ana pipeline pair",
        ),
        (
            "Feedback/eval",
            "Product metrics",
            "Eval runner/metrics",
            "Regression review",
        ),
        (
            "DevOps",
            "App integration/review",
            "Terraform/OIDC/apply",
            "Evidence + destroy",
        ),
        (
            "Dokümantasyon",
            "Product/demo/ADR",
            "Runbook/evaluation",
            "README/mimari sunum",
        ),
    ]
    columns = [54, 180, 306, 432, 558]
    positions = [74 + 10.6 * index for index in range(len(rows) + 1)]
    for index, row in enumerate(rows):
        fill_top(
            target,
            54,
            positions[index],
            504,
            positions[index + 1] - positions[index],
            BLUE if index == 0 else (WHITE if index % 2 else LIGHT_GRAY),
        )
        color = WHITE if index == 0 else BLACK
        font = "Arial-Bold" if index == 0 else "Arial"
        top = positions[index] + 1.5
        for column, value in enumerate(row):
            text_top(
                target,
                columns[column] + 5,
                top,
                value,
                font=font,
                size=6.7,
                color=color,
            )
    draw_grid(target, columns, positions)


PAGE_DRAWERS = {
    1: draw_page_1,
    2: draw_page_2,
    3: draw_page_3,
    4: draw_page_4,
    5: draw_page_5,
    7: draw_page_7,
    14: draw_page_14,
    17: draw_page_17,
    20: draw_page_20,
    21: draw_page_21,
    23: draw_page_23,
    24: draw_page_24,
    27: draw_page_27,
}

REDACTIONS = {
    1: [(78, 296, 534, 370), (52, 427, 560, 469)],
    2: [(53, 267, 383, 285)],
    3: [(54, 288.55, 558, 309.05), (54, 353, 558, 402), (54, 410, 558, 459)],
    4: [(390, 434.45, 558, 456.25)],
    5: [(306, 105.55, 558, 127.35), (222, 652.05, 390, 673.85)],
    7: [(390, 80.35, 558, 211.15)],
    14: [(53, 168, 558, 514)],
    17: [
        (54, 395, 558, 411),
        (54, 539, 558, 555),
        (54, 642, 558, 658),
        (54, 702, 558, 718),
    ],
    20: [(52, 312, 561, 728)],
    21: [(53, 49, 559, 169)],
    23: [(53, 49, 559, 227), (54, 390.65, 558, 410.05)],
    24: [(53, 337, 559, 355)],
    27: [(53, 49, 559, 181)],
}


def build_overlay(drawer: object) -> PdfReader:
    stream = BytesIO()
    target = canvas.Canvas(stream, pagesize=(PAGE_WIDTH, PAGE_HEIGHT))
    drawer(target)  # type: ignore[operator]
    target.save()
    stream.seek(0)
    return PdfReader(stream)


def redact_original_content(source: Path) -> BytesIO:
    """Remove the superseded text rather than only painting over it.

    PyMuPDF is intentionally a documentation-time dependency. Run this script
    with an ephemeral dependency, for example:

        uv run --with PyMuPDF python scripts/revise_project_plan_for_aws.py ...
    """

    try:
        import pymupdf  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "PyMuPDF is required for true PDF redaction. "
            "Run with `uv run --with PyMuPDF python ...`."
        ) from exc

    document: Any = pymupdf.open(source)
    for page_number, rectangles in REDACTIONS.items():
        page = document[page_number - 1]
        for x0, top, x1, bottom in rectangles:
            page.add_redact_annot(
                pymupdf.Rect(x0, top, x1, bottom),
                fill=(1, 1, 1),
                cross_out=False,
            )
        page.apply_redactions(images=0, graphics=0, text=0)

    result = BytesIO(
        document.tobytes(
            garbage=4,
            deflate=True,
            clean=True,
        )
    )
    document.close()
    result.seek(0)
    return result


def validate_source_pdf(source: Path) -> PdfReader:
    """Validate the immutable layout contract before using page coordinates."""

    if not source.is_file():
        raise ValueError(
            f"Unsupported source PDF: {source} is not a readable file. "
            f"Expected the {EXPECTED_SOURCE_PAGE_COUNT}-page AWS Revision "
            f"{EXPECTED_SOURCE_REVISION} PDF from commit {EXPECTED_SOURCE_COMMIT}."
        )

    try:
        reader = PdfReader(source)
        page_count = len(reader.pages)
        title = reader.metadata.title if reader.metadata is not None else None
        first_page_text = reader.pages[0].extract_text() if page_count else ""
    except Exception as exc:
        raise ValueError(
            f"Unsupported source PDF: {source} could not be read. Expected the "
            f"{EXPECTED_SOURCE_PAGE_COUNT}-page AWS Revision "
            f"{EXPECTED_SOURCE_REVISION} PDF from commit {EXPECTED_SOURCE_COMMIT}."
        ) from exc

    mismatches: list[str] = []
    if page_count != EXPECTED_SOURCE_PAGE_COUNT:
        mismatches.append(
            f"page count {page_count!r} (expected {EXPECTED_SOURCE_PAGE_COUNT})"
        )
    if title != EXPECTED_SOURCE_TITLE:
        mismatches.append(f"title {title!r} (expected {EXPECTED_SOURCE_TITLE!r})")
    if EXPECTED_SOURCE_REVISION_MARKER not in (first_page_text or ""):
        mismatches.append(
            "first-page revision marker missing "
            f"(expected {EXPECTED_SOURCE_REVISION_MARKER!r})"
        )

    if mismatches:
        details = "; ".join(mismatches)
        raise ValueError(
            f"Unsupported source PDF: {details}. Recover the exact Revision "
            f"{EXPECTED_SOURCE_REVISION} source from Git commit "
            f"{EXPECTED_SOURCE_COMMIT} using the README instructions before "
            "running this generator."
        )

    return reader


def revise_pdf(source: Path, output: Path) -> None:
    if source.resolve() == output.resolve():
        raise ValueError(
            "Source and output must differ so the original can be reviewed."
        )

    validate_source_pdf(source)
    register_fonts()
    redacted_source = redact_original_content(source)
    reader = PdfReader(redacted_source)
    if len(reader.pages) != EXPECTED_SOURCE_PAGE_COUNT:
        raise RuntimeError(
            "PDF redaction unexpectedly changed the source page count: "
            f"expected {EXPECTED_SOURCE_PAGE_COUNT}, found {len(reader.pages)}."
        )

    writer = PdfWriter()
    for page_number, page in enumerate(reader.pages, start=1):
        drawer = PAGE_DRAWERS.get(page_number)
        if drawer is not None:
            overlay = build_overlay(drawer)
            page.merge_page(overlay.pages[0], over=True)
        writer.add_page(page)

    metadata: dict[str, Any] = dict(reader.metadata or {})
    metadata.update(
        {
            "/Title": "SupportFlow AI - Uçtan Uca Proje Planı (AWS Revision 1.3)",
            "/Author": "Emir and Eray",
            "/Subject": (
                "12 haftalık geliştirme ve kısa ömürlü AWS production-shaped "
                "portföy kanıt planı"
            ),
            "/Keywords": (
                "SupportFlow AI, AWS, ECS, Fargate, RDS, ElastiCache, S3, "
                "Terraform, OIDC, ephemeral portfolio evidence"
            ),
        }
    )
    writer.add_metadata(metadata)

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as output_file:
        writer.write(output_file)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate Revision 1.3 from the 29-page AWS Revision 1.2 PDF "
            f"stored at Git commit {EXPECTED_SOURCE_COMMIT}."
        ),
        epilog=(
            "The source must have the expected page count, metadata title, and "
            "first-page Revision 1.2 marker. See README.md for the binary-safe "
            "source recovery command."
        ),
    )
    parser.add_argument(
        "source",
        type=Path,
        help="29-page AWS Revision 1.2 source recovered from commit 4b869d2",
    )
    parser.add_argument("output", type=Path, help="Revision 1.3 output path")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    revise_pdf(args.source, args.output)


if __name__ == "__main__":
    main()
