"""Local, cloud-free walkthrough of the pipeline's decision logic: read an invoice's
already-extracted fields, validate, three-way match, route, and — only if the routing
decision is STRAIGHT_THROUGH — hand off a payment instruction.

This is NOT the real ingestion pipeline (that needs Azure Document Intelligence, Blob
Storage, and Service Bus — Phase 2, not built yet: see tasks.md). It skips straight to
"OCR already happened" and constructs the extracted fields directly, exactly as
`DocumentIntelligenceExtractor.extract()` would have populated them. Everything from here
down — validation, matching, routing, payment hand-off — is the real, tested production
code path, run against two scenarios: a clean invoice (goes straight through, gets a
payment instruction) and a flagged one (stops at EXCEPTION, gets none).

Per spec.md §5 ("Why payment execution is excluded"): the payment instruction below is
never executed. It is the structured artifact this system hands to the customer's own
treasury/ERP system, which is the thing that actually moves money.

Run with:
    PYTHONPATH=src .venv/bin/python scripts/demo_process_invoice.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

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

# A GSTIN that passes both the format regex and the mod-36 checksum BR-09 enforces —
# verified against the standard algorithm, not invented (same one tests/unit/factories.py
# uses, chosen here independently so this script has no test-code dependency).
VALID_GSTIN = "27AAPFU0939F1ZV"


@dataclass
class NoDuplicatesLookup:
    """Structural stand-in for `core.persistence.DuplicateLookup` — this demo has exactly
    one invoice in flight at a time, so there is never a prior document to find."""

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


def acme_vendor() -> Vendor:
    """A services vendor with no PO requirement (AC-06.5) and processing history, so it's
    eligible for straight-through processing. `bank_account` is what the payment
    instruction will actually reference — never anything read off the invoice (FR-085)."""
    return Vendor(
        vendor_id="v-acme-001",
        name="Acme Supplies Pvt Ltd",
        normalised_name="acme supplies pvt ltd",
        tax_id=VALID_GSTIN,
        bank_account="HDFC0001234-000111222333",
        requires_po=False,
        invoices_processed=50,
    )


def clean_invoice() -> Invoice:
    """Everything a human or an OCR engine would read the same way, all fields
    high-confidence, arithmetic exact — the case this system exists to stop routing to a
    human (README §3)."""
    return Invoice(
        invoice_id="inv-demo-clean-1",
        document_id="doc-demo-clean-1",
        vendor_id="v-acme-001",
        fields={
            "vendor_name": _field("vendor_name", "Acme Supplies Pvt Ltd"),
            "vendor_tax_id": _field("vendor_tax_id", VALID_GSTIN),
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


def flagged_invoice() -> Invoice:
    """The same vendor, but the stated total doesn't reconcile against subtotal + tax —
    BR-03 — exactly the issue the Review UI demo (src/web) shows for this same scenario."""
    invoice = clean_invoice()
    invoice.invoice_id = "inv-demo-flagged-1"
    invoice.document_id = "doc-demo-flagged-1"
    invoice.fields["invoice_number"] = _field("invoice_number", "INV-1042")
    invoice.fields["total"] = _field("total", Decimal("1200.00"))  # should be 1180.00
    invoice.fields["amount_due"] = _field("amount_due", Decimal("1200.00"))
    return invoice


def process(invoice: Invoice, vendor: Vendor, adapter: MockPaymentInstructionAdapter) -> None:
    print(f"\n=== {invoice.invoice_id} ({invoice.invoice_number}) ===")

    ctx = ValidationContext(
        invoice=invoice,
        vendor_resolution=VendorResolution(vendor=vendor, method="exact_tax_id", score=100.0),
        duplicate_lookup=NoDuplicatesLookup(),
        today=date(2026, 8, 15),
    )
    issues = validate(ctx)
    failed = [i for i in issues if not i.passed]
    for issue in failed:
        print(f"  [{issue.severity.value.upper()}] {issue.rule_id}: {issue.message}")

    match = match_invoice(invoice, po=None, vendor_requires_po=vendor.requires_po)
    print(f"  3-way match: {match.status.value} — {match.message}")

    result = route(invoice, issues, match, vendor, RoutingPolicy(shadow_mode=False))
    print(f"  Routing decision: {result.decision.value} ({result.reason})")
    print(f"  Overall confidence: {result.overall_confidence:.2f}")

    if result.decision != RoutingDecision.STRAIGHT_THROUGH:
        print("  -> No payment instruction. This invoice needs a human (Review UI).")
        return

    instruction = build_payment_instruction(
        invoice, vendor, result, approved_by="system", now=datetime.now()
    )
    adapter.hand_off(instruction)
    print("  -> STRAIGHT_THROUGH. Payment instruction handed off:")
    print(f"       invoice_reference:   {instruction.invoice_reference}")
    print(f"       vendor_reference:    {instruction.vendor_reference}")
    print(f"       vendor_bank_account: {instruction.vendor_bank_account}  (from vendor master)")
    print(f"       amount:              {instruction.amount} {instruction.currency}")
    print(f"       due_date:            {instruction.due_date}")
    print(f"       approval_evidence:   {instruction.approval_evidence}")
    print("  (This is a mock hand-off — no transfer is initiated. See spec.md §5.)")


def main() -> None:
    vendor = acme_vendor()
    adapter = MockPaymentInstructionAdapter()
    process(clean_invoice(), vendor, adapter)
    process(flagged_invoice(), vendor, adapter)


if __name__ == "__main__":
    main()
