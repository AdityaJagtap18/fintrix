"""Shared test data builders. Not a test file itself (no `test_` prefix)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from core.models import BoundingRegion, ExtractedField, FieldSource, Invoice, LineItem, Vendor

# A GSTIN that passes both the format regex and the mod-36 checksum in
# core.validation.rules — verified against the standard algorithm, not invented.
VALID_GSTIN = "27AAPFU0939F1ZV"


def make_field(
    name: str,
    value: object,
    confidence: float = 0.97,
    *,
    source: FieldSource = FieldSource.MODEL,
    page: int = 1,
) -> ExtractedField:
    return ExtractedField(
        name=name,
        value=value,
        confidence=confidence,
        page=page,
        region=BoundingRegion(page=page, polygon=(0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0)),
        source=source,
    )


def make_invoice(
    invoice_id: str = "inv-1",
    document_id: str = "doc-1",
    *,
    vendor_name: str = "Acme Supplies Pvt Ltd",
    vendor_tax_id: str | None = VALID_GSTIN,
    invoice_number: str = "INV-1001",
    invoice_date: date = date(2026, 8, 1),
    due_date: date | None = date(2026, 8, 31),
    po_number: str | None = None,
    currency: str = "INR",
    subtotal: Decimal | None = Decimal("1000.00"),
    tax_total: Decimal | None = Decimal("180.00"),
    total: Decimal | None = Decimal("1180.00"),
    amount_due: Decimal | None = Decimal("1180.00"),
    confidence: float = 0.97,
    lines: list[LineItem] | None = None,
    extra_fields: dict[str, ExtractedField] | None = None,
) -> Invoice:
    fields: dict[str, ExtractedField] = {
        "vendor_name": make_field("vendor_name", vendor_name, confidence),
    }
    if vendor_tax_id is not None:
        fields["vendor_tax_id"] = make_field("vendor_tax_id", vendor_tax_id, confidence)
    fields["invoice_number"] = make_field("invoice_number", invoice_number, confidence)
    fields["invoice_date"] = make_field("invoice_date", invoice_date, confidence)
    if due_date is not None:
        fields["due_date"] = make_field("due_date", due_date, confidence)
    if po_number is not None:
        fields["po_number"] = make_field("po_number", po_number, confidence)
    fields["currency"] = make_field("currency", currency, confidence)
    if subtotal is not None:
        fields["subtotal"] = make_field("subtotal", subtotal, confidence)
    if tax_total is not None:
        fields["tax_total"] = make_field("tax_total", tax_total, confidence)
    if total is not None:
        fields["total"] = make_field("total", total, confidence)
    if amount_due is not None:
        fields["amount_due"] = make_field("amount_due", amount_due, confidence)
    if extra_fields:
        fields.update(extra_fields)

    return Invoice(
        invoice_id=invoice_id,
        document_id=document_id,
        fields=fields,
        lines=lines or [],
    )


def make_vendor(
    vendor_id: str = "v-acme-001",
    *,
    name: str = "Acme Supplies Pvt Ltd",
    normalised_name: str = "acme supplies pvt ltd",
    tax_id: str | None = VALID_GSTIN,
    address: str | None = None,
    bank_account: str | None = "HDFC0001234-000111222333",
    currency: str | None = "INR",
    requires_po: bool = False,
    auto_threshold: float | None = None,
    review_threshold: float | None = None,
    max_auto_amount: Decimal | None = None,
    invoices_processed: int = 50,
    is_active: bool = True,
) -> Vendor:
    return Vendor(
        vendor_id=vendor_id,
        name=name,
        normalised_name=normalised_name,
        tax_id=tax_id,
        address=address,
        bank_account=bank_account,
        currency=currency,
        requires_po=requires_po,
        auto_threshold=auto_threshold,
        review_threshold=review_threshold,
        max_auto_amount=max_auto_amount,
        invoices_processed=invoices_processed,
        is_active=is_active,
    )


def make_line(
    line_number: int = 1,
    *,
    description: str = "Widget",
    quantity: Decimal = Decimal("10"),
    unit_price: Decimal = Decimal("100.00"),
    amount: Decimal | None = None,
    tax_rate: Decimal | None = Decimal("18"),
    product_code: str | None = None,
    row_confidence: float = 0.95,
    matched_po_line_id: str | None = None,
) -> LineItem:
    if amount is None:
        amount = quantity * unit_price
    return LineItem(
        line_number=line_number,
        description=description,
        quantity=quantity,
        unit_price=unit_price,
        amount=amount,
        tax_rate=tax_rate,
        product_code=product_code,
        row_confidence=row_confidence,
        matched_po_line_id=matched_po_line_id,
    )
