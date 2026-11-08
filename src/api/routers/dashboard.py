"""Dashboard metrics for the FINTRIX overview screen — every number here is computed from
the actual seeded invoice store (`api.inmemory_repositories`), not a literal constant.
With only a dozen demo invoices seeded, the day-by-day processing-volume series is
genuinely sparse rather than a smooth curve; that's real data at this sample size, not a
rendering bug, and we chose not to synthesize a denser fake series to make the chart look
busier.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from api.inmemory_repositories import InMemoryInvoiceStore, get_invoice_store
from api.seed_data import InvoiceRecord

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])

Store = Annotated[InMemoryInvoiceStore, Depends(get_invoice_store)]

_APPROVED_LIKE = {"approved", "processed"}
_ATTENTION_LIKE = {"needs_review", "flagged"}


def _has_low_confidence_field(record: InvoiceRecord, threshold: float = 0.7) -> bool:
    return any(f.effective_confidence < threshold for f in record.invoice.fields.values())


@router.get("/metrics")
def dashboard_metrics(store: Store) -> dict[str, Any]:
    records = store.list()
    today = date.today()

    total_processed = len(records)
    awaiting_review = sum(1 for r in records if r.status == "needs_review")
    approved_this_month = sum(
        (r.invoice.total or Decimal("0"))
        for r in records
        if r.status in _APPROVED_LIKE
        and r.created_at.year == today.year
        and r.created_at.month == today.month
    )
    flagged = sum(1 for r in records if r.status == "flagged")
    exceptions_pct = round((flagged / total_processed) * 100, 1) if total_processed else 0.0

    window_start = today - timedelta(days=30)
    processing_volume = []
    day = window_start
    while day <= today:
        count = sum(1 for r in records if r.created_at.date() == day)
        processing_volume.append({"date": day.isoformat(), "count": count})
        day += timedelta(days=1)

    status_counts: dict[str, int] = {}
    for r in records:
        status_counts[r.status] = status_counts.get(r.status, 0) + 1

    recent = sorted(records, key=lambda r: r.created_at, reverse=True)[:6]

    needs_attention = []
    for r in records:
        if r.status not in _ATTENTION_LIKE:
            continue
        if _has_low_confidence_field(r):
            needs_attention.append({
                "kind": "low_confidence",
                "invoiceId": r.invoice.invoice_id,
                "title": "Low-confidence extraction",
                "description": f"{r.invoice.invoice_id} has fields below the confidence threshold",
            })
        else:
            needs_attention.append({
                "kind": "validation",
                "invoiceId": r.invoice.invoice_id,
                "title": "Needs review",
                "description": f"{r.invoice.invoice_id} is flagged for manual review",
            })

    return {
        "totalProcessed": total_processed,
        "awaitingReview": awaiting_review,
        "approvedThisMonth": str(approved_this_month),
        "exceptionsPct": exceptions_pct,
        "processingVolume": processing_volume,
        "statusBreakdown": status_counts,
        "recentInvoices": [
            {
                "invoiceId": r.invoice.invoice_id,
                "vendorName": r.vendor_name_display,
                "invoiceDate": (
                    r.invoice.invoice_date.isoformat() if r.invoice.invoice_date else None
                ),
                "amount": str(r.invoice.total) if r.invoice.total is not None else None,
                "status": r.status,
            }
            for r in recent
        ],
        "needsAttention": needs_attention[:5],
    }
