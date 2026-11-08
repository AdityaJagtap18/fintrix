"""Demo dataset for the screens that have no real pipeline behind them yet (dashboard,
invoice listing, vendor directory). Everything downstream of `core.models` here is real
domain data — real `Decimal` amounts, real `ExtractedField`/`LineItem` objects — so the
validation rules in `core.validation.rules` run against it for real (see
`routers/invoices.py`). Only the *existence* of these particular invoices is fabricated;
nothing here fakes a computation that should have been real.

Swap this module for real repositories once T-047 (SQL) lands; nothing above the
`InMemoryInvoiceStore` / `InMemoryVendorStore` interfaces in `inmemory_repositories.py`
needs to change when it does.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from core.models import ExtractedField, FieldSource, Invoice, LineItem, Vendor


@dataclass(slots=True)
class SeedUser:
    email: str
    password: str
    name: str
    role: str


SEED_USERS: tuple[SeedUser, ...] = (
    SeedUser(
        email="aditya@company.com",
        password="fintrix123",  # nosec B106
        name="Aditya Jagtap",
        role="Finance Ops",
    ),
    SeedUser(
        email="priya@company.com",
        password="fintrix123",  # nosec B106
        name="Priya Nair",
        role="Reviewer",
    ),
)


def _field(
    name: str,
    value: object,
    confidence: float,
    *,
    needs_review: bool = False,
    source: FieldSource = FieldSource.MODEL,
) -> ExtractedField:
    return ExtractedField(
        name=name, value=value, confidence=confidence, source=source, needs_review=needs_review
    )


@dataclass(slots=True)
class InvoiceRecord:
    """An `Invoice` plus the display/ops metadata no pipeline computes yet (which vendor
    it's "for" by name, when it landed, a human-facing status label). Kept separate from
    `core.models.Invoice` itself so core stays exactly what the constitution says it is."""

    invoice: Invoice
    vendor_name_display: str
    status: str  # "approved" | "needs_review" | "flagged" | "rejected" | "processed"
    created_at: datetime
    pdf_url: str


SEED_VENDORS: tuple[Vendor, ...] = (
    Vendor(
        vendor_id="v-techbuild",
        name="TechBuild Solutions Pvt. Ltd.",
        normalised_name="techbuild solutions",
        tax_id="27AABCT1234F1Z5",
        invoices_processed=42,
    ),
    Vendor(
        vendor_id="v-acme",
        name="Acme Supplies Pvt Ltd",
        normalised_name="acme supplies",
        tax_id="27AAACA5678Q1Z3",
        invoices_processed=63,
    ),
    Vendor(
        vendor_id="v-global",
        name="Global Supplies Ltd.",
        normalised_name="global supplies",
        tax_id="19AAGCG4455P1Z8",
        invoices_processed=18,
    ),
    Vendor(
        vendor_id="v-nexora",
        name="Nexora Analytics Pvt. Ltd.",
        normalised_name="nexora analytics",
        tax_id="27AAECN9876K1Z2",
        invoices_processed=27,
    ),
    Vendor(
        vendor_id="v-infraworks",
        name="InfraWorks",
        normalised_name="infraworks",
        tax_id="29AAFCI2233R1Z9",
        invoices_processed=11,
    ),
    Vendor(
        vendor_id="v-alpha",
        name="Alpha Enterprises",
        normalised_name="alpha enterprises",
        tax_id="07AAJCA7788S1Z4",
        invoices_processed=9,
    ),
    Vendor(
        vendor_id="v-vertex",
        name="Vertex Facilities",
        normalised_name="vertex facilities",
        tax_id="24AAKCV3344T1Z1",
        invoices_processed=15,
    ),
)


def _build_invoices() -> list[InvoiceRecord]:
    records: list[InvoiceRecord] = []

    # INV-2026-0842 — TechBuild: the ₹240 line-item/total mismatch used in the review
    # workspace reference. Deliberately high confidence everywhere except the mismatch
    # itself so the validation card, not a confidence flag, is what surfaces it.
    inv = Invoice(
        invoice_id="INV-2026-0842",
        document_id="doc-0842",
        vendor_id="v-techbuild",
        lines=[
            LineItem(1, "Cloud Infrastructure Support (Managed Services - Nov 2026)",
                      Decimal("1"), Decimal("60000.00"), Decimal("60000.00"),
                      product_code="998315", row_confidence=0.99),
            LineItem(2, "Data Integration & ETL Maintenance",
                      Decimal("1"), Decimal("25000.00"), Decimal("25000.00"),
                      product_code="998319", row_confidence=0.98),
            LineItem(3, "Reporting & Dashboard Enhancements",
                      Decimal("1"), Decimal("15000.00"), Decimal("15000.00"),
                      product_code="998319", row_confidence=0.98),
        ],
    )
    inv.fields = {
        "vendor_name": _field("vendor_name", "TechBuild Solutions Pvt. Ltd.", 0.99),
        "invoice_number": _field("invoice_number", "INV-2026-0842", 0.99),
        "invoice_date": _field("invoice_date", date(2026, 11, 6), 0.98),
        "due_date": _field("due_date", date(2026, 11, 21), 0.98),
        "vendor_tax_id": _field("vendor_tax_id", "27AABCT1234F1Z5", 0.99),
        "subtotal": _field("subtotal", Decimal("100000.00"), 0.98),
        "tax_total": _field("tax_total", Decimal("18000.00"), 0.98),
        # Mismatched on purpose: 100000 + 18000 = 118000, but the stated total is 118240.
        "total": _field("total", Decimal("118240.00"), 0.90, needs_review=True),
    }
    records.append(InvoiceRecord(inv, "TechBuild Solutions Pvt. Ltd.", "needs_review",
                                  datetime(2026, 11, 6, 10, 24), "/sample-invoice.pdf"))

    # INV-1042 — Acme: the original mock scenario (subtotal+tax vs total mismatch),
    # kept so the one demo PDF (public/sample-invoice.pdf) still lines up with its
    # hand-placed bounding regions.
    inv = Invoice(invoice_id="inv-demo-1", document_id="doc-demo-1", vendor_id="v-acme")
    inv.fields = {
        "vendor_name": _field("vendor_name", "Acme Supplies Pvt Ltd", 0.98),
        "invoice_number": _field("invoice_number", "INV-1042", 0.99),
        "invoice_date": _field("invoice_date", date(2026, 8, 15), 0.94),
        "subtotal": _field("subtotal", Decimal("48250.00"), 0.61, needs_review=True),
        "total": _field("total", Decimal("51000.00"), 0.58, needs_review=True),
    }
    inv.lines = [
        LineItem(1, "Office chairs (ergonomic)", Decimal("25"), Decimal("1850.00"),
                  Decimal("46250.00"), product_code="FUR-201", row_confidence=0.93),
        LineItem(2, "Desk lamps", Decimal("20"), Decimal("100.00"),
                  Decimal("2000.00"), product_code="FUR-330", row_confidence=0.90),
    ]
    records.append(InvoiceRecord(inv, "Acme Supplies Pvt Ltd", "needs_review",
                                  datetime(2026, 8, 15, 9, 10), "/sample-invoice.pdf"))

    plain = [
        ("INV-2026-0836", "v-acme", "Acme Supplies Pvt Ltd", date(2026, 10, 31),
         Decimal("124560.00"), "approved"),
        ("INV-2026-0835", "v-nexora", "Global Tech Solutions", date(2026, 10, 31),
         Decimal("348900.00"), "approved"),
        ("INV-2026-0834", "v-techbuild", "Prime Logistics", date(2026, 10, 30),
         Decimal("82740.00"), "needs_review"),
        ("INV-2026-0833", "v-infraworks", "OfficeHub Traders", date(2026, 10, 30),
         Decimal("56200.00"), "approved"),
        ("INV-2026-0832", "v-vertex", "Vertex Facilities", date(2026, 10, 29),
         Decimal("208400.00"), "flagged"),
        ("INV-0841", "v-global", "Global Supplies Ltd.", date(2026, 11, 5),
         Decimal("72500.00"), "needs_review"),
        ("INV-0840", "v-nexora", "Nexora Analytics Pvt. Ltd.", date(2026, 11, 3),
         Decimal("245000.00"), "approved"),
        ("INV-0839", "v-infraworks", "InfraWorks", date(2026, 11, 2),
         Decimal("75800.00"), "approved"),
        ("INV-0838", "v-alpha", "Alpha Enterprises", date(2026, 11, 1),
         Decimal("150000.00"), "rejected"),
        ("INV-0837", "v-vertex", "Vertex Facilities", date(2026, 10, 29),
         Decimal("48200.00"), "processed"),
    ]
    for invoice_number, vendor_id, vendor_display, inv_date, total, status in plain:
        subtotal = (total / Decimal("1.18")).quantize(Decimal("0.01"))
        tax = total - subtotal
        inv = Invoice(invoice_id=invoice_number, document_id=f"doc-{invoice_number}",
                       vendor_id=vendor_id)
        inv.fields = {
            "vendor_name": _field("vendor_name", vendor_display, 0.97),
            "invoice_number": _field("invoice_number", invoice_number, 0.99),
            "invoice_date": _field("invoice_date", inv_date, 0.96),
            "subtotal": _field("subtotal", subtotal, 0.95),
            "tax_total": _field("tax_total", tax, 0.95),
            "total": _field("total", total, 0.95 if status != "flagged" else 0.55,
                             needs_review=status in ("needs_review", "flagged")),
        }
        inv.lines = [
            LineItem(1, f"Services rendered — {invoice_number}", Decimal("1"), subtotal,
                      subtotal, row_confidence=0.95),
        ]
        records.append(InvoiceRecord(
            inv, vendor_display, status,
            datetime.combine(inv_date, datetime.min.time()),
            "/sample-invoice.pdf",
        ))

    return records


SEED_INVOICES: list[InvoiceRecord] = _build_invoices()
