"""Tesseract OCR adapter — the FOSS replacement for the Azure Document Intelligence
adapter this project used to ship (removed when the project went fully open-source; see
git history for `core/extraction/document_intelligence.py` if you need the old version).

Like its predecessor, this is the one sanctioned place in `core` that reaches for an
external engine; everything downstream only ever sees `ExtractedField`s (constitution
Article II, NFR-050 — swapping engines is one new adapter class, not a rewrite).

Honest tradeoff, not a bug: Tesseract does OCR (pixels -> text + word boxes), not
structured field extraction. There is no trained "read an invoice" model here, so field
mapping below is regex/heuristic over OCR'd text anchored on common invoice label words
("Invoice No", "Total Due", "Due Date", ...). Expect materially lower recall than a
vendor's prebuilt-invoice model, especially on:
  - vendor_name (heuristic: first substantial text line on page 1 — no layout model to
    confirm it's actually a letterhead, not a running header or watermark)
  - line items (heuristic: rows with a trailing currency-looking number and at least one
    other number before it — no table-structure model, so merged/split cells or unusual
    column orders will misparse)
`classify()` is a keyword match, not a trained classifier — it only distinguishes
"looks like a credit note" / "looks like an invoice" / "neither", each at a fixed,
deliberately mediocre confidence, so routing code that thresholds on confidence doesn't
mistake a keyword hit for the real thing.

Requires the `tesseract-ocr` system binary (via `pytesseract`) and `poppler-utils` (via
`pdf2image`) to be installed wherever this runs — see the Dockerfile for the apt packages.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from dateutil import parser as date_parser

from core.extraction.base import (
    ClassificationResult,
    DocumentExtractor,
    DocumentType,
    ExtractionRequest,
    ExtractionResult,
    PermanentExtractionError,
    TransientExtractionError,
)
from core.models import BoundingRegion, ExtractedField, FieldSource, Invoice, LineItem

# pdf2image renders at this DPI; word pixel boxes are divided by it to get the same
# "inches on the page" unit BoundingRegion already uses for the old DI adapter, so the
# review UI's overlay math doesn't need to know which engine produced a given field.
RENDER_DPI = 200

# Keyword-match confidence is deliberately well below anything a trained model would
# report, so nothing downstream mistakes "the word 'invoice' appears somewhere" for an
# actual classification decision.
_CLASSIFY_MATCH_CONFIDENCE = 0.6
_CLASSIFY_NO_MATCH_CONFIDENCE = 0.0

# Same reasoning for the weakest heuristics (vendor name, line items): a low fixed
# confidence so routing's existing low-confidence-review path catches these by default.
_HEURISTIC_CONFIDENCE = 0.3


@dataclass(frozen=True, slots=True)
class OcrWord:
    """One word Tesseract found, already normalized to this module's units."""

    text: str
    confidence: float  # 0..1 (Tesseract reports 0..100)
    page: int
    left: float
    top: float
    width: float
    height: float


@dataclass(frozen=True, slots=True)
class _Line:
    page: int
    words: tuple[OcrWord, ...]
    text: str
    # Character offset of each word's first character within `text`, same order as words.
    word_offsets: tuple[int, ...]


PdfToImages = Callable[[str], list[Any]]  # blob_uri -> one page image per page, in order
OcrImage = Callable[[Any, int], list[OcrWord]]  # (page image, 1-based page number) -> words


def _default_pdf_to_images(blob_uri: str) -> list[Any]:
    from pdf2image import convert_from_path  # heavy/optional import, only needed live

    return list(convert_from_path(blob_uri, dpi=RENDER_DPI))


def _default_ocr_image(image: Any, page: int) -> list[OcrWord]:
    import pytesseract

    data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
    words: list[OcrWord] = []
    for i, text in enumerate(data["text"]):
        if not text.strip():
            continue
        conf = float(data["conf"][i])
        if conf < 0:  # Tesseract uses -1 for boxes it found but couldn't read as text
            continue
        words.append(
            OcrWord(
                text=text,
                confidence=conf / 100.0,
                page=page,
                left=float(data["left"][i]) / RENDER_DPI,
                top=float(data["top"][i]) / RENDER_DPI,
                width=float(data["width"][i]) / RENDER_DPI,
                height=float(data["height"][i]) / RENDER_DPI,
            )
        )
    return words


class TesseractExtractor(DocumentExtractor):
    def __init__(
        self,
        *,
        pdf_to_images: PdfToImages = _default_pdf_to_images,
        ocr_image: OcrImage = _default_ocr_image,
    ) -> None:
        self._pdf_to_images = pdf_to_images
        self._ocr_image = ocr_image

    def classify(self, request: ExtractionRequest) -> ClassificationResult:
        words = self._ocr_pages(request, max_pages=1)
        text = " ".join(w.text for w in words).lower()
        if "credit note" in text or "credit memo" in text:
            return ClassificationResult(
                document_type=DocumentType.CREDIT_NOTE, confidence=_CLASSIFY_MATCH_CONFIDENCE
            )
        if "invoice" in text:
            return ClassificationResult(
                document_type=DocumentType.INVOICE, confidence=_CLASSIFY_MATCH_CONFIDENCE
            )
        return ClassificationResult(
            document_type=DocumentType.OTHER, confidence=_CLASSIFY_NO_MATCH_CONFIDENCE
        )

    def extract(self, request: ExtractionRequest) -> ExtractionResult:
        words = self._ocr_pages(request)
        if not words:
            raise PermanentExtractionError("OCR produced no text for this document")

        lines = _group_into_lines(words)
        fields = _map_fields(lines)
        line_items = _map_line_items(lines)

        invoice = Invoice(
            invoice_id=f"inv-{request.document_id}",
            document_id=request.document_id,
            fields=fields,
            lines=line_items,
        )
        return ExtractionResult(
            invoice=invoice,
            model_id="tesseract",
            model_version=_tesseract_version(),
            # FR-024: kept verbatim so reprocessing (e.g. a field-mapping bug fix) never
            # re-runs OCR. Plain dicts, not OcrWord instances, so this round-trips as JSON.
            raw_response={
                "words": [
                    {
                        "text": w.text,
                        "confidence": w.confidence,
                        "page": w.page,
                        "left": w.left,
                        "top": w.top,
                        "width": w.width,
                        "height": w.height,
                    }
                    for w in words
                ]
            },
        )

    def _ocr_pages(self, request: ExtractionRequest, max_pages: int | None = None) -> list[OcrWord]:
        try:
            images = self._pdf_to_images(request.blob_uri)
        except FileNotFoundError as exc:
            raise PermanentExtractionError(f"document not found: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 — poppler's own errors aren't a stable type
            # A page that genuinely won't render (corrupt/unsupported file) is never going
            # to render on retry either — permanent, same reasoning as DI's 4xx mapping.
            raise PermanentExtractionError(f"could not render document pages: {exc}") from exc

        if max_pages is not None:
            images = images[:max_pages]

        words: list[OcrWord] = []
        for page_number, image in enumerate(images, start=1):
            try:
                words.extend(self._ocr_image(image, page_number))
            except Exception as exc:  # noqa: BLE001 — pytesseract's own errors aren't a stable type
                # Missing binary / engine crash is an environment problem, not the
                # document's fault — worth retrying once the environment is fixed.
                raise TransientExtractionError(f"OCR engine failed: {exc}") from exc
        return words


def _tesseract_version() -> str:
    try:
        import pytesseract

        return str(pytesseract.get_tesseract_version())
    except Exception:  # noqa: BLE001 — version lookup is best-effort, never fatal
        return "unknown"


# ------------------------------------------------------------------------------------
# Word -> line grouping
# ------------------------------------------------------------------------------------
_LINE_TOLERANCE_INCHES = 0.08  # ~2mm: words within this vertical band are "the same line"


def _group_into_lines(words: list[OcrWord]) -> list[_Line]:
    by_page: dict[int, list[OcrWord]] = {}
    for w in words:
        by_page.setdefault(w.page, []).append(w)

    lines: list[_Line] = []
    for page in sorted(by_page):
        page_words = sorted(by_page[page], key=lambda w: (w.top, w.left))
        current: list[OcrWord] = []
        current_top: float | None = None
        for w in page_words:
            if current_top is None or abs(w.top - current_top) <= _LINE_TOLERANCE_INCHES:
                current.append(w)
                current_top = w.top if current_top is None else current_top
            else:
                lines.append(_build_line(page, current))
                current = [w]
                current_top = w.top
        if current:
            lines.append(_build_line(page, current))
    return lines


def _build_line(page: int, words: list[OcrWord]) -> _Line:
    ordered = sorted(words, key=lambda w: w.left)
    text_parts: list[str] = []
    offsets: list[int] = []
    cursor = 0
    for w in ordered:
        offsets.append(cursor)
        text_parts.append(w.text)
        cursor += len(w.text) + 1  # +1 for the joining space
    return _Line(
        page=page, words=tuple(ordered), text=" ".join(text_parts), word_offsets=tuple(offsets)
    )


def _words_in_span(line: _Line, start: int, end: int) -> list[OcrWord]:
    return [
        w
        for w, offset in zip(line.words, line.word_offsets, strict=True)
        if offset < end and offset + len(w.text) > start
    ]


def _region_for(words: list[OcrWord]) -> BoundingRegion | None:
    if not words:
        return None
    left = min(w.left for w in words)
    top = min(w.top for w in words)
    right = max(w.left + w.width for w in words)
    bottom = max(w.top + w.height for w in words)
    polygon = (left, top, right, top, right, bottom, left, bottom)
    return BoundingRegion(page=words[0].page, polygon=polygon)


def _confidence_for(words: list[OcrWord]) -> float:
    if not words:
        return 0.0
    return sum(w.confidence for w in words) / len(words)


# ------------------------------------------------------------------------------------
# Scalar field mapping — one label pattern per canonical field (FR-020's canonical names,
# same contract the old Document Intelligence adapter produced).
# ------------------------------------------------------------------------------------
_LABELS: dict[str, re.Pattern[str]] = {
    "invoice_number": re.compile(r"invoice\s*(?:no\.?|number|#|id)\s*[:\-]?\s*", re.IGNORECASE),
    "po_number": re.compile(
        r"(?:purchase\s*order|p\.?o\.?)\s*(?:no\.?|number|#)?\s*[:\-]?\s*", re.IGNORECASE
    ),
    "vendor_tax_id": re.compile(
        r"(?:gstin|tax\s*id|vat\s*(?:no\.?|number)?)\s*[:\-]?\s*", re.IGNORECASE
    ),
    "invoice_date": re.compile(r"invoice\s*date\s*[:\-]?\s*", re.IGNORECASE),
    "due_date": re.compile(r"due\s*date\s*[:\-]?\s*", re.IGNORECASE),
    "payment_terms": re.compile(r"payment\s*terms?\s*[:\-]?\s*", re.IGNORECASE),
    "subtotal": re.compile(r"sub\s*-?\s*total\s*[:\-]?\s*", re.IGNORECASE),
    "tax_total": re.compile(r"(?:tax|gst|vat)\s*(?:total|amount)?\s*[:\-]?\s*", re.IGNORECASE),
    "amount_due": re.compile(r"(?:amount|balance)\s*due\s*[:\-]?\s*", re.IGNORECASE),
    # Checked after the more specific labels above so "sub total" / "amount due" don't
    # also satisfy this broader "total" pattern first.
    "total": re.compile(r"(?:grand\s*)?total\s*[:\-]?\s*", re.IGNORECASE),
}

_DATE_FIELDS = {"invoice_date", "due_date"}
_MONEY_FIELDS = {"subtotal", "tax_total", "total", "amount_due"}
_MONEY_PATTERN = re.compile(r"[\d][\d,]*\.\d{1,2}|[\d][\d,]*")
_CURRENCY_SYMBOLS = {"₹": "INR", "$": "USD", "€": "EUR", "£": "GBP"}


def _parse_decimal(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.replace(",", ""))
    except InvalidOperation:
        return None


def _map_fields(lines: list[_Line]) -> dict[str, ExtractedField]:
    fields: dict[str, ExtractedField] = {}

    for canonical_name, label in _LABELS.items():
        if canonical_name in fields:
            continue
        for line in lines:
            match = label.search(line.text)
            if match is None:
                continue
            rest = line.text[match.end() :].strip()
            if not rest:
                continue
            extracted = _extract_value(canonical_name, rest, line, match.end())
            if extracted is not None:
                fields[canonical_name] = extracted
                break

    vendor_name = _guess_vendor_name(lines)
    if vendor_name is not None:
        fields["vendor_name"] = vendor_name

    currency = _guess_currency(lines)
    if currency is not None:
        fields["currency"] = currency

    return fields


def _extract_value(
    canonical_name: str, rest_of_line: str, line: _Line, value_start_offset: int
) -> ExtractedField | None:
    if canonical_name in _DATE_FIELDS:
        try:
            parsed = date_parser.parse(rest_of_line, fuzzy=True, dayfirst=True).date()
        except (ValueError, OverflowError):
            return None
        words = _words_in_span(line, value_start_offset, len(line.text))
        region = _region_for(words)
        return ExtractedField(
            name=canonical_name,
            value=parsed,
            confidence=_confidence_for(words),
            page=region.page if region else None,
            region=region,
            source=FieldSource.MODEL,
        )

    if canonical_name in _MONEY_FIELDS:
        money_match = _MONEY_PATTERN.search(rest_of_line)
        if money_match is None:
            return None
        decimal_value = _parse_decimal(money_match.group())
        if decimal_value is None:
            return None
        match_start = value_start_offset + money_match.start()
        match_end = value_start_offset + money_match.end()
        words = _words_in_span(line, match_start, match_end)
        region = _region_for(words)
        return ExtractedField(
            name=canonical_name,
            value=decimal_value,
            confidence=_confidence_for(words),
            page=region.page if region else None,
            region=region,
            source=FieldSource.MODEL,
        )

    # Everything else (invoice_number, po_number, vendor_tax_id, payment_terms) is read
    # as the first whitespace-delimited token after the label.
    value = rest_of_line.split()[0] if rest_of_line.split() else None
    if not value:
        return None
    words = _words_in_span(line, value_start_offset, value_start_offset + len(value))
    region = _region_for(words)
    return ExtractedField(
        name=canonical_name,
        value=value,
        confidence=_confidence_for(words),
        page=region.page if region else None,
        region=region,
        source=FieldSource.MODEL,
    )


def _guess_vendor_name(lines: list[_Line]) -> ExtractedField | None:
    # Heuristic: the first substantial text on page 1 that isn't itself a labeled field —
    # a common invoice layout puts the vendor's letterhead above everything else, but
    # nothing here actually confirms that, hence the deliberately low fixed confidence.
    for line in lines:
        if line.page != 1:
            break
        stripped = line.text.strip()
        if len(stripped) < 3:
            continue
        if any(label.search(stripped) for label in _LABELS.values()):
            continue
        return ExtractedField(
            name="vendor_name",
            value=stripped,
            confidence=_HEURISTIC_CONFIDENCE,
            page=line.page,
            region=_region_for(list(line.words)),
            source=FieldSource.MODEL,
        )
    return None


def _guess_currency(lines: list[_Line]) -> ExtractedField | None:
    for line in lines:
        for symbol, code in _CURRENCY_SYMBOLS.items():
            if symbol in line.text:
                return ExtractedField(
                    name="currency",
                    value=code,
                    confidence=_HEURISTIC_CONFIDENCE,
                    source=FieldSource.MODEL,
                )
    return None


# ------------------------------------------------------------------------------------
# Line items (FR-021) — best-effort row detection, no table-structure model available.
# ------------------------------------------------------------------------------------
_ROW_NUMBER_PATTERN = re.compile(r"[\d][\d,]*\.\d{1,2}")


def _map_line_items(lines: list[_Line]) -> list[LineItem]:
    items: list[LineItem] = []
    line_number = 0
    for line in lines:
        numbers = _ROW_NUMBER_PATTERN.findall(line.text)
        # A plausible line-item row has a description plus at least a quantity, unit
        # price, and amount — i.e. three money/number-shaped tokens trailing the text.
        if len(numbers) < 3:
            continue
        if any(label.search(line.text) for label in _LABELS.values()):
            continue  # this is a totals/subtotal row, not a line item
        description = _ROW_NUMBER_PATTERN.split(line.text)[0].strip(" :-")
        if not description:
            continue
        quantity = _parse_decimal(numbers[-3]) or Decimal("0")
        unit_price = _parse_decimal(numbers[-2]) or Decimal("0")
        amount = _parse_decimal(numbers[-1]) or Decimal("0")
        line_number += 1
        items.append(
            LineItem(
                line_number=line_number,
                description=description,
                quantity=quantity,
                unit_price=unit_price,
                amount=amount,
                tax_rate=None,
                product_code=None,
                row_confidence=_confidence_for(list(line.words)),
            )
        )
    return items
