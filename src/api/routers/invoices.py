"""Invoice listing + detail endpoints for the FINTRIX UI.

Validation on the detail endpoint runs the *real* arithmetic rules from
`core.validation.rules` (BR-02, BR-03) against the invoice's actual line items and totals
— the "line-item total differs by ₹X" card on the review screen is a live computation,
not a hardcoded string. The full rule catalogue (`rules.validate`) also covers vendor
fuzzy-matching and duplicate detection, which need a real vendor master / document store
to be meaningful; running only the two arithmetic rules here is a deliberate, narrower
slice rather than faking the rest.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.inmemory_repositories import InMemoryInvoiceStore, get_invoice_store
from api.seed_data import InvoiceRecord
from core.models import VendorResolution
from core.validation.rules import (
    ValidationContext,
    line_items_sum_to_subtotal,
    subtotal_plus_tax_equals_total,
)

router = APIRouter(prefix="/api/v1/invoices", tags=["invoices"])

Store = Annotated[InMemoryInvoiceStore, Depends(get_invoice_store)]

_NULL_VENDOR_RESOLUTION = VendorResolution(vendor=None, method="none", score=0.0)


class _NullDuplicateLookup:
    def exact_duplicate_exists(
        self, vendor_tax_id: str, invoice_number: str, exclude_invoice_id: str | None = None
    ) -> bool:
        return False

    def find_near_duplicates(
        self, vendor_id: str, amount: Decimal, invoice_date: date, window_days: int
    ) -> list[str]:
        return []


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


def _summary(record: InvoiceRecord) -> dict[str, Any]:
    inv = record.invoice
    return {
        "invoiceId": inv.invoice_id,
        "vendorName": record.vendor_name_display,
        "invoiceDate": _jsonable(inv.invoice_date),
        "amount": _jsonable(inv.total),
        "status": record.status,
    }


def _run_arithmetic_rules(record: InvoiceRecord) -> list[dict[str, Any]]:
    ctx = ValidationContext(
        invoice=record.invoice,
        vendor_resolution=_NULL_VENDOR_RESOLUTION,
        duplicate_lookup=_NullDuplicateLookup(),
        today=date.today(),
    )
    issues = [line_items_sum_to_subtotal(ctx), subtotal_plus_tax_equals_total(ctx)]
    return [
        {
            "ruleId": issue.rule_id,
            "severity": issue.severity.value,
            "passed": issue.passed,
            "message": issue.message,
            "fields": list(issue.fields),
            "explanation": issue.message if not issue.passed else None,
        }
        for issue in issues
        if not issue.passed
    ]


def _detail(record: InvoiceRecord) -> dict[str, Any]:
    inv = record.invoice
    fields = [
        {
            "name": f.name,
            "value": _jsonable(f.value),
            "confidence": f.effective_confidence,
            "page": f.page,
            "region": (
                {"page": f.region.page, "polygon": list(f.region.polygon)} if f.region else None
            ),
            "source": f.source.value,
            "needsReview": f.needs_review,
            "suggestedValue": None,
        }
        for f in inv.fields.values()
    ]
    lines = [
        {
            "lineNumber": line.line_number,
            "description": line.description,
            "quantity": _jsonable(line.quantity),
            "unitPrice": _jsonable(line.unit_price),
            "amount": _jsonable(line.amount),
            "productCode": line.product_code,
            "rowConfidence": line.row_confidence,
        }
        for line in inv.lines
    ]
    issues = _run_arithmetic_rules(record)

    # BR-03's suggested total (subtotal + tax), surfaced as the one-click "accept
    # suggestion" value on the total field — AC-02.3 in the original review screen.
    for issue in issues:
        if issue["ruleId"] == "BR-03" and inv.subtotal is not None and inv.tax_total is not None:
            suggested = inv.subtotal + inv.tax_total
            for f in fields:
                if f["name"] == "total":
                    f["suggestedValue"] = _jsonable(suggested)

    return {
        "invoiceId": inv.invoice_id,
        "documentId": inv.document_id,
        "pdfUrl": record.pdf_url,
        "vendorName": record.vendor_name_display,
        "invoiceNumber": inv.invoice_number,
        "status": record.status,
        "createdAt": record.created_at.isoformat(),
        "fields": fields,
        "lines": lines,
        "issues": issues,
    }


@router.get("")
def list_invoices(
    store: Store,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    vendor: str | None = None,
    q: str | None = None,
    page: int = 1,
    page_size: int = 10,
) -> dict[str, Any]:
    records = store.list()
    if status_filter:
        records = [r for r in records if r.status == status_filter]
    if vendor:
        records = [r for r in records if vendor.lower() in r.vendor_name_display.lower()]
    if q:
        needle = q.lower()
        records = [
            r
            for r in records
            if needle in r.invoice.invoice_id.lower() or needle in r.vendor_name_display.lower()
        ]
    records.sort(key=lambda r: r.created_at, reverse=True)

    total = len(records)
    start = max(page - 1, 0) * page_size
    page_records = records[start : start + page_size]
    return {
        "items": [_summary(r) for r in page_records],
        "total": total,
        "page": page,
        "pageSize": page_size,
    }


@router.get("/{invoice_id}")
def get_invoice(invoice_id: str, store: Store) -> dict[str, Any]:
    record = store.get(invoice_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="invoice not found")
    return _detail(record)
