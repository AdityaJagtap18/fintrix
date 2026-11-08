"""Tests the Tesseract OCR adapter's line-grouping, field-mapping, and error-translation
logic against fake `pdf_to_images`/`ocr_image` callables — never the real Tesseract binary
or poppler. Same trust level as the old Document Intelligence adapter's tests: this proves
the mapping is correct for a given word layout, not that real-world OCR output will ever
look this clean.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from core.extraction.base import (
    ClassificationResult,
    DocumentType,
    ExtractionRequest,
    PermanentExtractionError,
    TransientExtractionError,
)
from core.extraction.tesseract_ocr import OcrWord, TesseractExtractor
from core.models import FieldSource


def word(
    text: str, index: int, top: float = 0.1, page: int = 1, confidence: float = 0.9
) -> OcrWord:
    # `index` spaces words 1 inch apart left-to-right — far enough that width never
    # matters for ordering, close enough in `top` to land on the same OCR'd line.
    return OcrWord(
        text=text,
        confidence=confidence,
        page=page,
        left=index * 1.0,
        top=top,
        width=0.5,
        height=0.12,
    )


def line(
    texts: list[str], top: float = 0.1, page: int = 1, confidence: float = 0.9
) -> list[OcrWord]:
    return [word(t, i, top=top, page=page, confidence=confidence) for i, t in enumerate(texts)]


def make_extractor(
    pages: dict[int, list[OcrWord]] | None = None,
    *,
    pdf_to_images_error: Exception | None = None,
    ocr_image_error: Exception | None = None,
) -> TesseractExtractor:
    pages = pages or {}

    def fake_pdf_to_images(blob_uri: str) -> list[int]:
        if pdf_to_images_error is not None:
            raise pdf_to_images_error
        return sorted(pages) if pages else [1]

    def fake_ocr_image(image: int, page: int) -> list[OcrWord]:
        if ocr_image_error is not None:
            raise ocr_image_error
        return pages.get(page, [])

    return TesseractExtractor(pdf_to_images=fake_pdf_to_images, ocr_image=fake_ocr_image)


def make_request(document_id: str = "doc-1") -> ExtractionRequest:
    return ExtractionRequest(
        document_id=document_id,
        blob_uri="blob://raw/doc-1.pdf",
        content_hash="hash-1",
        page_count=1,
    )


# --------------------------------------------------------------------------------------
# classify()
# --------------------------------------------------------------------------------------
def test_classify_matches_invoice_keyword():
    extractor = make_extractor({1: line(["Invoice", "INV-1001"])})

    classification = extractor.classify(make_request())

    assert classification == ClassificationResult(
        document_type=DocumentType.INVOICE, confidence=0.6
    )


def test_classify_matches_credit_note_keyword():
    extractor = make_extractor({1: line(["Credit", "Note", "CN-5"])})

    classification = extractor.classify(make_request())

    assert classification.document_type == DocumentType.CREDIT_NOTE


def test_classify_no_keyword_match_returns_other_zero_confidence():
    extractor = make_extractor({1: line(["Statement", "of", "Account"])})

    classification = extractor.classify(make_request())

    assert classification == ClassificationResult(document_type=DocumentType.OTHER, confidence=0.0)


# --------------------------------------------------------------------------------------
# extract() — scalar field mapping
# --------------------------------------------------------------------------------------
def test_extract_maps_invoice_number_with_confidence_and_bounding_region():
    extractor = make_extractor(
        {
            1: [
                *line(["TechBuild", "Solutions"], top=0.1, confidence=0.95),
                *line(["Invoice", "No:", "INV-1001"], top=0.4, confidence=0.97),
            ]
        }
    )

    extraction = extractor.extract(make_request("doc-1"))

    invoice = extraction.invoice
    assert invoice.document_id == "doc-1"
    assert invoice.invoice_number == "INV-1001"
    assert invoice.fields["invoice_number"].confidence == pytest.approx(0.97)
    assert invoice.fields["invoice_number"].page == 1
    assert invoice.fields["invoice_number"].region is not None
    assert invoice.fields["invoice_number"].source == FieldSource.MODEL
    assert extraction.model_id == "tesseract"
    assert extraction.raw_response  # FR-024: raw OCR output retained for reprocessing


def test_extract_maps_total_as_decimal():
    extractor = make_extractor({1: line(["Total:", "1180.00"])})

    extraction = extractor.extract(make_request())

    assert extraction.invoice.total == Decimal("1180.00")


def test_extract_maps_date_fields_to_python_dates():
    extractor = make_extractor(
        {
            1: [
                *line(["Invoice", "Date:", "01/08/2026"], top=0.1),
                *line(["Due", "Date:", "31/08/2026"], top=0.3),
            ]
        }
    )

    extraction = extractor.extract(make_request())

    assert extraction.invoice.invoice_date == date(2026, 8, 1)
    assert extraction.invoice.due_date == date(2026, 8, 31)
    assert isinstance(extraction.invoice.invoice_date, date)


def test_extract_guesses_currency_from_symbol():
    extractor = make_extractor({1: line(["Total:", "₹1180.00"])})

    extraction = extractor.extract(make_request())

    assert extraction.invoice.currency == "INR"


def test_extract_guesses_vendor_name_from_first_unlabeled_line():
    extractor = make_extractor(
        {
            1: [
                *line(["TechBuild", "Solutions", "Pvt", "Ltd"], top=0.1),
                *line(["Invoice", "No:", "INV-1001"], top=0.4),
            ]
        }
    )

    extraction = extractor.extract(make_request())

    assert extraction.invoice.vendor_name == "TechBuild Solutions Pvt Ltd"
    assert extraction.invoice.fields["vendor_name"].source == FieldSource.MODEL


def test_extract_missing_field_is_absent_not_a_default_value():
    extractor = make_extractor({1: line(["Invoice", "No:", "INV-1001"])})

    extraction = extractor.extract(make_request())

    assert "due_date" not in extraction.invoice.fields
    assert extraction.invoice.due_date is None


# --------------------------------------------------------------------------------------
# extract() — line items (best-effort row detection, FR-021)
# --------------------------------------------------------------------------------------
def test_extract_maps_line_item_rows():
    extractor = make_extractor(
        {
            1: [
                *line(["Widget", "A", "10.00", "50.00", "500.00"], top=0.5),
                *line(["Widget", "B", "2.00", "20.00", "40.00"], top=0.7),
            ]
        }
    )

    extraction = extractor.extract(make_request())

    lines = extraction.invoice.lines
    assert len(lines) == 2
    assert lines[0].line_number == 1
    assert lines[0].description == "Widget A"
    assert lines[0].quantity == Decimal("10.00")
    assert lines[0].unit_price == Decimal("50.00")
    assert lines[0].amount == Decimal("500.00")
    assert lines[1].line_number == 2
    assert lines[1].description == "Widget B"


def test_extract_with_no_row_shaped_lines_has_empty_lines():
    extractor = make_extractor({1: line(["Invoice", "No:", "INV-1001"])})

    extraction = extractor.extract(make_request())

    assert extraction.invoice.lines == []


def test_extract_excludes_totals_rows_from_line_items():
    extractor = make_extractor(
        {
            1: [
                *line(["Widget", "A", "10.00", "50.00", "500.00"], top=0.5),
                *line(["Subtotal", "Due", "500.00", "500.00", "500.00"], top=0.7),
            ]
        }
    )

    extraction = extractor.extract(make_request())

    assert len(extraction.invoice.lines) == 1
    assert extraction.invoice.lines[0].description == "Widget A"


# --------------------------------------------------------------------------------------
# extract() — no OCR'd text
# --------------------------------------------------------------------------------------
def test_extract_with_no_words_raises_permanent_error():
    extractor = make_extractor({1: []})

    with pytest.raises(PermanentExtractionError):
        extractor.extract(make_request())


# --------------------------------------------------------------------------------------
# Error translation (Article XII: permanent vs transient)
# --------------------------------------------------------------------------------------
def test_unrenderable_document_is_permanent():
    extractor = make_extractor(pdf_to_images_error=ValueError("not a valid PDF"))

    with pytest.raises(PermanentExtractionError):
        extractor.extract(make_request())


def test_missing_document_is_permanent():
    extractor = make_extractor(pdf_to_images_error=FileNotFoundError("blob gone"))

    with pytest.raises(PermanentExtractionError):
        extractor.extract(make_request())


def test_ocr_engine_failure_is_transient():
    extractor = make_extractor({1: []}, ocr_image_error=RuntimeError("tesseract binary crashed"))

    with pytest.raises(TransientExtractionError):
        extractor.extract(make_request())
