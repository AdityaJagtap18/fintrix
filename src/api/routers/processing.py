"""Read-only view over the core validate -> match -> route -> payment-instruction
pipeline, for the "Processing Results" screen in the Review UI — plus a way to submit a
new invoice from that screen and see it processed the same way.

This is demo/dev-only, same spirit as `api/dependencies.py`'s in-memory placeholders: real
ingestion (Phase 2) needs Azure Document Intelligence, so `POST /process` never actually
reads the uploaded file's content — there is no OCR wired in yet. The caller supplies the
field values a real extraction would have produced; the file itself is accepted only as a
filename for display (`source_filename`), never parsed. The downstream logic (validation,
matching, routing, payment hand-off) is the real, tested production code, not a mock of
itself — including duplicate detection: every invoice processed in this process's lifetime
(the two seeded demo invoices included) is remembered, so submitting the same
vendor-tax-id/invoice-number pair twice genuinely trips BR-04.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from api.dependencies import get_document_intelligence_extractor
from core.extraction.base import (
    ExtractionRequest,
    PermanentExtractionError,
    TransientExtractionError,
)
from core.extraction.document_intelligence import DocumentIntelligenceExtractor
from core.matching.three_way import match_invoice
from core.models import (
    BoundingRegion,
    ExtractedField,
    Invoice,
    LineItem,
    RoutingDecision,
    Vendor,
    VendorResolution,
)
from core.payment.instruction import MockPaymentInstructionAdapter, build_payment_instruction
from core.routing.confidence_router import RoutingPolicy, route
from core.validation.rules import ValidationContext, validate

router = APIRouter(prefix="/api/v1/processing", tags=["processing"])

# Same checksum-valid GSTIN as scripts/demo_process_invoice.py — see that file's comment.
_VALID_GSTIN = "27AAPFU0939F1ZV"
_DEMO_NOW = datetime(2026, 8, 29, 9, 0, 0)  # fixed, not datetime.now() — see module docstring


def _field(name: str, value: object, confidence: float) -> ExtractedField:
    return ExtractedField(
        name=name,
        value=value,
        confidence=confidence,
        page=1,
        region=BoundingRegion(page=1, polygon=(0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0)),
    )


@dataclass
class _DuplicateStore:
    """A real `DuplicateLookup` (core.persistence), backed by every (vendor_tax_id,
    invoice_number) pair this process has ever recorded — not a stub that always returns
    False. `record` is called once per processed invoice, seeded invoices included, so a
    genuine re-submission trips BR-04 rather than only ever passing."""

    seen: set[tuple[str, str]]

    def exact_duplicate_exists(
        self, vendor_tax_id: str, invoice_number: str, exclude_invoice_id: str | None = None
    ) -> bool:
        return (vendor_tax_id, invoice_number) in self.seen

    def find_near_duplicates(
        self, vendor_id: str, amount: Decimal, invoice_date: date, window_days: int
    ) -> list[str]:
        return []

    def record(self, vendor_tax_id: str | None, invoice_number: str | None) -> None:
        if vendor_tax_id and invoice_number:
            self.seen.add((vendor_tax_id, invoice_number))


def _demo_vendor() -> Vendor:
    return Vendor(
        vendor_id="v-acme-001",
        name="Acme Supplies Pvt Ltd",
        normalised_name="acme supplies pvt ltd",
        tax_id=_VALID_GSTIN,
        bank_account="HDFC0001234-000111222333",
        requires_po=False,
        invoices_processed=50,
    )


def _clean_invoice() -> Invoice:
    return Invoice(
        invoice_id="inv-demo-clean-1",
        document_id="doc-demo-clean-1",
        vendor_id="v-acme-001",
        fields={
            "vendor_name": _field("vendor_name", "Acme Supplies Pvt Ltd", 0.97),
            "vendor_tax_id": _field("vendor_tax_id", _VALID_GSTIN, 0.97),
            "invoice_number": _field("invoice_number", "INV-1001", 0.97),
            "invoice_date": _field("invoice_date", date(2026, 8, 1), 0.97),
            "due_date": _field("due_date", date(2026, 8, 31), 0.97),
            "currency": _field("currency", "INR", 0.97),
            "subtotal": _field("subtotal", Decimal("1000.00"), 0.97),
            "tax_total": _field("tax_total", Decimal("180.00"), 0.97),
            "total": _field("total", Decimal("1180.00"), 0.97),
            "amount_due": _field("amount_due", Decimal("1180.00"), 0.97),
        },
        lines=[
            LineItem(
                line_number=1,
                description="Consulting services — August",
                quantity=Decimal("10"),
                unit_price=Decimal("100.00"),
                amount=Decimal("1000.00"),
                tax_rate=Decimal("18"),
                row_confidence=0.95,
            )
        ],
    )


def _flagged_invoice() -> Invoice:
    invoice = _clean_invoice()
    invoice.invoice_id = "inv-demo-flagged-1"
    invoice.document_id = "doc-demo-flagged-1"
    invoice.fields["invoice_number"] = _field("invoice_number", "INV-1042", 0.97)
    invoice.fields["total"] = _field("total", Decimal("1200.00"), 0.97)  # should be 1180.00
    invoice.fields["amount_due"] = _field("amount_due", Decimal("1200.00"), 0.97)
    return invoice


class IssueDto(BaseModel):
    rule_id: str
    severity: str
    message: str


class PaymentInstructionDto(BaseModel):
    invoice_reference: str
    vendor_reference: str
    vendor_bank_account: str | None
    amount: Decimal | None
    currency: str | None
    due_date: date | None
    approval_evidence: str


class ProcessingResultDto(BaseModel):
    invoice_id: str
    invoice_number: str | None
    vendor_name: str | None
    decision: str
    reason: str
    overall_confidence: float
    match_status: str
    issues: list[IssueDto]
    payment_instruction: PaymentInstructionDto | None
    source_filename: str | None = None
    # "azure_document_intelligence" for a real POST /extract call, "manual_entry" for
    # POST /process or a seeded demo invoice — the UI shows this so results are never
    # ambiguous about where their data actually came from.
    extraction_method: str = "manual_entry"


class ProcessInvoiceRequest(BaseModel):
    """The fields a real OCR extraction would have populated (T-041), entered directly
    since Phase 2's Document Intelligence adapter has nowhere to run without a live Azure
    resource. `source_filename` is display-only — the file's bytes are never read."""

    vendor_name: str
    vendor_tax_id: str | None = None
    invoice_number: str
    invoice_date: date
    due_date: date | None = None
    po_number: str | None = None
    currency: str = "INR"
    subtotal: Decimal | None = None
    tax_total: Decimal | None = None
    total: Decimal
    amount_due: Decimal | None = None
    confidence: float = Field(default=0.97, ge=0.0, le=1.0)
    vendor_requires_po: bool = False
    vendor_invoices_processed: int = 50
    vendor_bank_account: str | None = "HDFC0001234-000111222333"
    source_filename: str | None = None


def _process(
    invoice: Invoice,
    vendor: Vendor,
    adapter: MockPaymentInstructionAdapter,
    duplicates: _DuplicateStore,
    *,
    source_filename: str | None = None,
    extraction_method: str = "manual_entry",
) -> ProcessingResultDto:
    ctx = ValidationContext(
        invoice=invoice,
        vendor_resolution=VendorResolution(vendor=vendor, method="exact_tax_id", score=100.0),
        duplicate_lookup=duplicates,
        today=date(2026, 8, 15),
    )
    issues = validate(ctx)
    failed = [i for i in issues if not i.passed]
    duplicates.record(invoice.vendor_tax_id, invoice.invoice_number)

    match = match_invoice(invoice, po=None, vendor_requires_po=vendor.requires_po)
    result = route(invoice, issues, match, vendor, RoutingPolicy(shadow_mode=False))

    instruction_dto = None
    if result.decision == RoutingDecision.STRAIGHT_THROUGH:
        instruction = build_payment_instruction(
            invoice, vendor, result, approved_by="system", now=_DEMO_NOW
        )
        adapter.hand_off(instruction)
        instruction_dto = PaymentInstructionDto(
            invoice_reference=instruction.invoice_reference,
            vendor_reference=instruction.vendor_reference,
            vendor_bank_account=instruction.vendor_bank_account,
            amount=instruction.amount,
            currency=instruction.currency,
            due_date=instruction.due_date,
            approval_evidence=instruction.approval_evidence,
        )

    return ProcessingResultDto(
        invoice_id=invoice.invoice_id,
        invoice_number=invoice.invoice_number,
        vendor_name=invoice.vendor_name,
        decision=result.decision.value,
        reason=result.reason,
        overall_confidence=result.overall_confidence,
        match_status=match.status.value,
        issues=[
            IssueDto(rule_id=i.rule_id, severity=i.severity.value, message=i.message)
            for i in failed
        ],
        payment_instruction=instruction_dto,
        source_filename=source_filename,
        extraction_method=extraction_method,
    )


def _invoice_from_request(request: ProcessInvoiceRequest, invoice_id: str) -> Invoice:
    c = request.confidence
    fields: dict[str, ExtractedField] = {
        "vendor_name": _field("vendor_name", request.vendor_name, c),
        "invoice_number": _field("invoice_number", request.invoice_number, c),
        "invoice_date": _field("invoice_date", request.invoice_date, c),
        "currency": _field("currency", request.currency, c),
        "total": _field("total", request.total, c),
    }
    if request.vendor_tax_id:
        fields["vendor_tax_id"] = _field("vendor_tax_id", request.vendor_tax_id, c)
    if request.due_date:
        fields["due_date"] = _field("due_date", request.due_date, c)
    if request.po_number:
        fields["po_number"] = _field("po_number", request.po_number, c)
    if request.subtotal is not None:
        fields["subtotal"] = _field("subtotal", request.subtotal, c)
    if request.tax_total is not None:
        fields["tax_total"] = _field("tax_total", request.tax_total, c)
    if request.amount_due is not None:
        fields["amount_due"] = _field("amount_due", request.amount_due, c)

    return Invoice(invoice_id=invoice_id, document_id=invoice_id, fields=fields)


class _VendorRegistry:
    """Stands in for the vendor master. T-047's Cosmos `CosmosVendorRepository` exists now,
    but this demo/dev router (see module docstring) deliberately stays in-memory: wiring
    extraction results through to real persistence is ingestion's job (T-049+), not this
    router's. Keyed by tax ID when Document Intelligence extracted one, else by vendor name.

    A vendor seen for the first time genuinely has zero processing history — there is no
    database of prior invoices to check — so it starts at `invoices_processed=0` and is
    correctly never eligible for straight-through (AC-01.3) until it's been seen again.
    `bank_account` starts as `None`: FR-085 forbids ever sourcing it from the invoice
    itself, and there's no real vendor master yet to supply a trustworthy value, so an
    extracted vendor's first several payment instructions will show no bank reference —
    that gap is honest, not a bug to paper over.
    """

    def __init__(self) -> None:
        self._by_key: dict[str, Vendor] = {}
        self._next_id = 1

    def resolve_or_create(self, name: str | None, tax_id: str | None) -> Vendor:
        key = tax_id or (name or "unknown-vendor").strip().lower()
        vendor = self._by_key.get(key)
        if vendor is not None:
            vendor.invoices_processed += 1
            return vendor
        vendor = Vendor(
            vendor_id=f"v-extracted-{self._next_id}",
            name=name or "Unknown Vendor",
            normalised_name=(name or "unknown vendor").strip().lower(),
            tax_id=tax_id,
            bank_account=None,
            requires_po=False,
            invoices_processed=0,
        )
        self._by_key[key] = vendor
        self._next_id += 1
        return vendor


class _ProcessingStore:
    """Everything processed in this process's lifetime — the two seeded demo invoices,
    plus every `POST /process` submission after them. Computed lazily so the first `GET
    /results` (not module import) is what seeds the demo invoices and the duplicate
    store — matters for test isolation, see test_processing_api.py."""

    def __init__(self) -> None:
        self._results: list[ProcessingResultDto] | None = None
        self._adapter = MockPaymentInstructionAdapter()
        self._duplicates = _DuplicateStore(seen=set())
        self._vendors = _VendorRegistry()

    def _ensure_seeded(self) -> list[ProcessingResultDto]:
        if self._results is None:
            vendor = _demo_vendor()
            self._results = [
                _process(_clean_invoice(), vendor, self._adapter, self._duplicates),
                _process(_flagged_invoice(), vendor, self._adapter, self._duplicates),
            ]
        return self._results

    def list_results(self) -> list[ProcessingResultDto]:
        return self._ensure_seeded()

    def submit(self, request: ProcessInvoiceRequest) -> ProcessingResultDto:
        results = self._ensure_seeded()
        invoice_id = f"inv-upload-{uuid4().hex[:8]}"
        invoice = _invoice_from_request(request, invoice_id)
        vendor = Vendor(
            vendor_id=f"v-upload-{invoice_id}",
            name=request.vendor_name,
            normalised_name=request.vendor_name.lower(),
            tax_id=request.vendor_tax_id,
            bank_account=request.vendor_bank_account,
            requires_po=request.vendor_requires_po,
            invoices_processed=request.vendor_invoices_processed,
        )
        result = _process(
            invoice,
            vendor,
            self._adapter,
            self._duplicates,
            source_filename=request.source_filename,
        )
        results.append(result)
        return result

    def extract_and_process(
        self, extractor: DocumentIntelligenceExtractor, content: bytes, filename: str
    ) -> ProcessingResultDto:
        results = self._ensure_seeded()
        document_id = f"upload-{uuid4().hex[:8]}"
        request = ExtractionRequest(
            document_id=document_id,
            blob_uri=f"local-upload://{filename}",  # placeholder — see ExtractionRequest.content
            content_hash="",  # T-044's cache isn't wired up here; nothing consumes this
            page_count=1,
            content=content,
        )
        try:
            extraction = extractor.extract(request)
        except TransientExtractionError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Document Intelligence is temporarily unavailable: {exc}",
            ) from exc
        except PermanentExtractionError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Document Intelligence could not read this file: {exc}",
            ) from exc

        invoice = extraction.invoice
        vendor = self._vendors.resolve_or_create(invoice.vendor_name, invoice.vendor_tax_id)
        result = _process(
            invoice,
            vendor,
            self._adapter,
            self._duplicates,
            source_filename=filename,
            extraction_method="azure_document_intelligence",
        )
        results.append(result)
        return result


_store = _ProcessingStore()


@router.get("/results", response_model=list[ProcessingResultDto])
def get_results() -> list[ProcessingResultDto]:
    return _store.list_results()


@router.post("/process", response_model=ProcessingResultDto)
def process_invoice(request: ProcessInvoiceRequest) -> ProcessingResultDto:
    return _store.submit(request)


Extractor = Annotated[DocumentIntelligenceExtractor, Depends(get_document_intelligence_extractor)]


@router.post("/extract", response_model=ProcessingResultDto)
async def extract_invoice(
    extractor: Extractor, file: Annotated[UploadFile, File()]
) -> ProcessingResultDto:
    """Real OCR: sends the uploaded file's bytes to Azure Document Intelligence's
    prebuilt-invoice model and runs whatever it extracts through the same validate ->
    match -> route -> payment-instruction pipeline as everything else on this router.
    Every field on the result genuinely came from the document — nothing here is typed in.
    """
    content = await file.read()
    return _store.extract_and_process(extractor, content, file.filename or "upload.pdf")
