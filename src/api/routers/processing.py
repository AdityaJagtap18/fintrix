"""Read-only view over the core validate -> match -> route -> payment-instruction
pipeline, for the "Processing Results" screen in the Review UI.

This is demo/dev-only, same spirit as `api/dependencies.py`'s in-memory placeholders: real
ingestion (Phase 2) needs Azure Document Intelligence, so the two invoices below have their
extracted fields constructed directly rather than actually read from a document — see
scripts/demo_process_invoice.py, which runs the identical scenario as a standalone CLI. The
downstream logic (validation, matching, routing, payment hand-off) is the real, tested
production code, not a mock of itself.

Results are computed once per process (module-level cache) rather than on every GET, so a
page reload doesn't re-run `hand_off` with a new timestamp and trip
`AlreadyHandedOffError` against the previous run's instruction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter
from pydantic import BaseModel

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


@dataclass
class _NoDuplicatesLookup:
    near: list[str] = field(default_factory=list)

    def exact_duplicate_exists(
        self, vendor_tax_id: str, invoice_number: str, exclude_invoice_id: str | None = None
    ) -> bool:
        return False

    def find_near_duplicates(
        self, vendor_id: str, amount: Decimal, invoice_date: date, window_days: int
    ) -> list[str]:
        return self.near


def _field(name: str, value: object, confidence: float = 0.97) -> ExtractedField:
    return ExtractedField(
        name=name,
        value=value,
        confidence=confidence,
        page=1,
        region=BoundingRegion(page=1, polygon=(0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0)),
    )


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
            "vendor_name": _field("vendor_name", "Acme Supplies Pvt Ltd"),
            "vendor_tax_id": _field("vendor_tax_id", _VALID_GSTIN),
            "invoice_number": _field("invoice_number", "INV-1001"),
            "invoice_date": _field("invoice_date", date(2026, 8, 1)),
            "due_date": _field("due_date", date(2026, 8, 31)),
            "currency": _field("currency", "INR"),
            "subtotal": _field("subtotal", Decimal("1000.00")),
            "tax_total": _field("tax_total", Decimal("180.00")),
            "total": _field("total", Decimal("1180.00")),
            "amount_due": _field("amount_due", Decimal("1180.00")),
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
    invoice.fields["invoice_number"] = _field("invoice_number", "INV-1042")
    invoice.fields["total"] = _field("total", Decimal("1200.00"))  # should be 1180.00
    invoice.fields["amount_due"] = _field("amount_due", Decimal("1200.00"))
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


def _process(
    invoice: Invoice, vendor: Vendor, adapter: MockPaymentInstructionAdapter
) -> ProcessingResultDto:
    ctx = ValidationContext(
        invoice=invoice,
        vendor_resolution=VendorResolution(vendor=vendor, method="exact_tax_id", score=100.0),
        duplicate_lookup=_NoDuplicatesLookup(),
        today=date(2026, 8, 15),
    )
    issues = validate(ctx)
    failed = [i for i in issues if not i.passed]

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
    )


class _DemoResultsCache:
    """Computed once per process, not once per request — a GET must not re-run
    `hand_off` with a fresh timestamp on every page load (see module docstring)."""

    def __init__(self) -> None:
        self._results: list[ProcessingResultDto] | None = None

    def get(self) -> list[ProcessingResultDto]:
        if self._results is None:
            vendor = _demo_vendor()
            adapter = MockPaymentInstructionAdapter()
            self._results = [
                _process(_clean_invoice(), vendor, adapter),
                _process(_flagged_invoice(), vendor, adapter),
            ]
        return self._results


_cache = _DemoResultsCache()


@router.get("/results", response_model=list[ProcessingResultDto])
def get_results() -> list[ProcessingResultDto]:
    return _cache.get()
