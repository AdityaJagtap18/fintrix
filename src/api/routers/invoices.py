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

import hashlib
import os
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import Response

from api.inmemory_repositories import (
    InMemoryInvoiceStore,
    InMemoryVendorStore,
    get_invoice_store,
    get_vendor_store,
)
from api.seed_data import InvoiceRecord
from core.extraction.base import ExtractionRequest, PermanentExtractionError
from core.extraction.classification import decide_disposition
from core.extraction.tesseract_ocr import TesseractExtractor
from core.matching.three_way import match_invoice
from core.matching.vendor import resolve_vendor
from core.models import RoutingDecision, VendorResolution
from core.routing.confidence_router import RoutingPolicy, route
from core.validation.rules import (
    ValidationContext,
    line_items_sum_to_subtotal,
    subtotal_plus_tax_equals_total,
    validate,
)

router = APIRouter(prefix="/api/v1/invoices", tags=["invoices"])

Store = Annotated[InMemoryInvoiceStore, Depends(get_invoice_store)]
VendorStore = Annotated[InMemoryVendorStore, Depends(get_vendor_store)]

# Uploaded PDFs live only in process memory, same lifetime as the demo invoice/vendor
# stores above — there is no real blob storage behind this demo (see README §2), so a
# redeploy or restart loses them exactly like everything else in these in-memory stores.
_UPLOADED_PDFS: dict[str, bytes] = {}

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


# Maps the router's real decision (plan.md §5) onto the UI's existing status vocabulary.
# REVIEW/FULL_REVIEW are confidence-driven — dashboard.py's needs-attention widget reads
# these as "low_confidence". EXCEPTION is a hard validation/match failure — it reads as
# "validation" there instead, which only matches invoices in {needs_review, flagged}, so
# EXCEPTION needs its own bucket rather than collapsing into the same one as REVIEW.
_DECISION_TO_STATUS: dict[RoutingDecision, str] = {
    RoutingDecision.STRAIGHT_THROUGH: "approved",
    RoutingDecision.REVIEW: "needs_review",
    RoutingDecision.FULL_REVIEW: "needs_review",
    RoutingDecision.EXCEPTION: "flagged",
    RoutingDecision.REJECTED: "rejected",
}


@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload_invoice(
    request: Request, store: Store, vendor_store: VendorStore, file: Annotated[UploadFile, File()]
) -> dict[str, Any]:
    """Runs a real PDF through the actual pipeline (classify -> OCR extract -> resolve
    vendor -> validate -> three-way match -> route) and adds the result to the invoice
    store, exactly like the orchestrator's stages 2-9 (functions/orchestrator) will once
    T-052/T-049 wire it to a real durable-task runtime — this just calls the same pure
    `core` functions directly, synchronously, since there's no queue/worker behind this
    demo to hand off to.
    """
    content = await file.read()
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="empty file")

    document_id = f"upload-{uuid.uuid4().hex[:12]}"
    _UPLOADED_PDFS[document_id] = content

    # TesseractExtractor reads from a path (pdf2image/poppler need a real file), not
    # bytes in memory — written under /tmp, same place any other scratch file for this
    # process would go. Removed again once OCR has read it; the bytes themselves stay in
    # _UPLOADED_PDFS for the review screen's PDF viewer to fetch later.
    tmp_path = f"/tmp/{document_id}.pdf"
    with open(tmp_path, "wb") as f:
        f.write(content)
    try:
        extraction_request = ExtractionRequest(
            document_id=document_id,
            blob_uri=tmp_path,
            content_hash=hashlib.sha256(content).hexdigest(),
            page_count=0,  # not known ahead of render; the Tesseract adapter doesn't use it
        )
        extractor = TesseractExtractor()

        classification = extractor.classify(extraction_request)
        disposition = decide_disposition(classification)
        if disposition.disposition.value == "quarantine":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"document quarantined: {disposition.reason}",
            )

        try:
            extraction = extractor.extract(extraction_request)
        except PermanentExtractionError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
    finally:
        os.remove(tmp_path)

    invoice = extraction.invoice
    vendor_resolution = resolve_vendor(invoice.vendor_name, invoice.vendor_tax_id, vendor_store)
    if vendor_resolution.vendor is not None:
        invoice.vendor_id = vendor_resolution.vendor.vendor_id

    issues = validate(
        ValidationContext(
            invoice=invoice,
            vendor_resolution=vendor_resolution,
            # BR-04/05 need a real document store to mean anything (duplicate-of-what?)
            # — an ad-hoc upload has no history to check against, same as the narrower
            # slice `_run_arithmetic_rules` already uses for seeded invoices.
            duplicate_lookup=_NullDuplicateLookup(),
            today=date.today(),
        )
    )
    match = match_invoice(
        invoice,
        po=None,  # no PO master behind this demo to resolve a reference against
        vendor_requires_po=(
            vendor_resolution.vendor.requires_po if vendor_resolution.vendor else True
        ),
    )
    policy = RoutingPolicy(shadow_mode=False)
    routing = route(invoice, issues, match, vendor_resolution.vendor, policy)

    vendor_name_display = (
        vendor_resolution.vendor.name
        if vendor_resolution.vendor
        else (invoice.vendor_name or "Unknown vendor")
    )
    record = InvoiceRecord(
        invoice=invoice,
        vendor_name_display=vendor_name_display,
        status=_DECISION_TO_STATUS.get(routing.decision, "needs_review"),
        created_at=datetime.now(),
        # Absolute URL, not a frontend-relative path like the seeded demo invoices use —
        # the frontend's PDF viewer fetches this directly, and frontend/backend are
        # separate origins in production (see docker-compose.prod.yml).
        pdf_url=f"{str(request.base_url).rstrip('/')}/api/v1/invoices/{invoice.invoice_id}/pdf",
    )
    store.add(record)
    return {
        "invoiceId": invoice.invoice_id,
        "decision": routing.decision.value,
        "reason": routing.reason,
    }


@router.get("/{invoice_id}/pdf")
def get_invoice_pdf(invoice_id: str, store: Store) -> Response:
    record = store.get(invoice_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="invoice not found")
    content = _UPLOADED_PDFS.get(record.invoice.document_id)
    if content is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="no stored PDF for this invoice"
        )
    return Response(content=content, media_type="application/pdf")
