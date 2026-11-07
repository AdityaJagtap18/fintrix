"""Read-only view over the core validate -> match -> route -> payment-instruction
pipeline, for the "Processing Results" screen in the Review UI — plus a way to submit a
new invoice from that screen and see it processed the same way.

`POST /process` still never reads an uploaded file's content — the caller supplies the
field values a real extraction would have produced, and the file itself is accepted only
as a filename for display (`source_filename`). `POST /extract` does the real thing, via
Azure Document Intelligence. Either way, everything downstream of "here is an Invoice and
a Vendor" is the real, tested production code, not a mock of itself — and as of T-047's
Cosmos wiring landing here, that includes persistence: every processed invoice and vendor
is genuinely saved to Cosmos DB (VendorRepository, InvoiceRepository), duplicate detection
(BR-04) queries real stored invoices rather than an in-memory set that forgets on restart,
and every routed decision gets an AuditLogWriter entry. What's still process-local is only
the *dashboard's own view* — this list, in this order, plus the two seeded demo invoices —
which still resets on restart; the domain data behind it does not.

One consequence worth being explicit about: `POST /process`, `POST /extract`,
`GET /results`, and `DELETE /results/{id}` now all depend on Cosmos DB being configured
(`COSMOS_ENDPOINT` set) — they fail loud with 503 otherwise, the same choice
`api/dependencies.py` already made for every other Cosmos-backed repository accessor.
"""

from __future__ import annotations

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import uuid4

from azure.cosmos.exceptions import CosmosResourceExistsError
from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from opentelemetry import trace
from pydantic import BaseModel, Field

from api.auth import (
    ensure_quota_available,
    get_current_username,
    get_session,
    record_documents_used,
)
from api.dependencies import (
    get_audit_log_writer,
    get_document_intelligence_extractor,
    get_duplicate_lookup,
    get_invoice_admin,
    get_invoice_repo,
    get_review_repo,
    get_vendor_repo,
)
from api.observability import metrics
from core.extraction.base import (
    ExtractionRequest,
    PermanentExtractionError,
    TransientExtractionError,
)
from core.extraction.cache import decide_cache_reuse
from core.extraction.document_intelligence import DocumentIntelligenceExtractor
from core.extraction.preprocessing import count_pages
from core.matching.three_way import match_invoice
from core.models import (
    BoundingRegion,
    ExtractedField,
    Invoice,
    LineItem,
    ReviewOutcome,
    RoutingDecision,
    RoutingResult,
    ValidationIssue,
    Vendor,
    VendorResolution,
)
from core.payment.instruction import MockPaymentInstructionAdapter, build_payment_instruction
from core.persistence import (
    AuditLogWriter,
    DuplicateLookup,
    InvoiceAdmin,
    InvoiceRepository,
    ReviewAssignmentRepository,
    VendorRepository,
)
from core.routing.confidence_router import RoutingPolicy, route
from core.validation.rules import ValidationContext, validate

router = APIRouter(
    prefix="/api/v1/processing", tags=["processing"], dependencies=[Depends(get_session)]
)

# Same checksum-valid GSTIN as scripts/demo_process_invoice.py — see that file's comment.
_VALID_GSTIN = "27AAPFU0939F1ZV"
_DEMO_NOW = datetime(2026, 8, 29, 9, 0, 0)  # fixed, not datetime.now() — see module docstring
# Same frozen reference _process()'s ValidationContext already used inline — named here so
# the summary/urgency math below (GET /summary) can't drift from what validation itself
# treats as "today".
_TODAY = date(2026, 8, 15)
# "Due soon" / urgent-by-deadline window for GET /summary.
_URGENT_DUE_WINDOW_DAYS = 3

# POST /extract/batch: capped concurrency, not "however many files got uploaded" — Azure's
# S0 Document Intelligence tier has a real per-second/concurrent-request quota, and firing
# every file at once just trades a slow batch for a wall of 429s. One retry on a throttled
# file (TransientExtractionError only — Article XII forbids retrying a billed permanent
# failure) is the "exponential backoff, never a tight retry loop" plan.md §4 already calls
# for; a fixed short delay is close enough for a handful of concurrent workers.
_BATCH_MAX_WORKERS = 4
_BATCH_RETRY_DELAY_SECONDS = 2.0

# Same 5 fields _invoice_from_request builds from a manual submission — the set this app
# actually treats as "a complete invoice" (BR-01 requires exactly these). Used only for
# the field_completion_rate telemetry below, not for validation itself.
_EXPECTED_INVOICE_FIELDS = ("vendor_name", "invoice_number", "invoice_date", "currency", "total")
_tracer = trace.get_tracer("invoice_iq.processing")


def _field_completion_rate(invoice: Invoice) -> float:
    present = sum(1 for name in _EXPECTED_INVOICE_FIELDS if invoice.value(name) not in (None, ""))
    return present / len(_EXPECTED_INVOICE_FIELDS)


def _field(name: str, value: object, confidence: float) -> ExtractedField:
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
    # Populated by GET /results from the *separate* review-assignment store (review.py) —
    # not something _ProcessingStore knows on its own. None until a reviewer has actually
    # submitted a decision on this invoice.
    review_outcome: str | None = None
    # The invoice's own extracted due_date — unlike payment_instruction.due_date, set
    # regardless of decision. due_date is an optional field (IMPORTANT_FIELDS, not
    # CRITICAL_FIELDS in confidence_router.py), so this is often None even for a
    # perfectly good invoice that simply never carried one.
    due_date: date | None = None


class RegionDto(BaseModel):
    page: int
    polygon: list[float]


class FieldDetailDto(BaseModel):
    name: str
    value: str | float | None
    confidence: float
    page: int | None
    region: RegionDto | None
    source: str
    needs_review: bool
    # AC-02.3: a rule-recomputed correction the reviewer can accept in one click — e.g.
    # BR-03's recomputed total. None when no failed rule suggested a value for this field.
    suggested_value: str | float | None


class ValidationIssueDetailDto(BaseModel):
    rule_id: str
    severity: str
    passed: bool
    message: str
    fields: list[str]


class InvoiceDetailDto(BaseModel):
    """What the Review page needs for one invoice — the fuller sibling of
    ProcessingResultDto, which only carries a summary. Powers GET /results/{invoice_id},
    replacing ReviewPage.tsx's hardcoded mockInvoice for anything actually processed here."""

    invoice_id: str
    document_id: str
    vendor_name: str | None
    invoice_number: str | None
    # None when this invoice has no real file behind it (manual entry, seeded demo data) —
    # only a real POST /extract upload has bytes to serve back.
    pdf_url: str | None
    fields: list[FieldDetailDto]
    issues: list[ValidationIssueDetailDto]


class BatchFileResultDto(BaseModel):
    """One file's outcome from POST /extract/batch. A batch is a list of these, not a
    single ProcessingResultDto — one throttled or unreadable file doesn't sink the rest,
    it just reports its own status."""

    filename: str
    status: str  # "success" | "error"
    result: ProcessingResultDto | None = None
    error: str | None = None


class ProcessInvoiceRequest(BaseModel):
    """The fields a real OCR extraction would have populated (T-041), entered directly
    for exercising specific routing outcomes without needing a matching real document.
    `source_filename` is display-only — the file's bytes are never read."""

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


def _scalar_to_wire(value: Any) -> str | float | None:
    if value is None or isinstance(value, (int, float)):
        return value
    return str(value)  # Decimal, date — same "stringify anything non-numeric" rule the
    # frontend's ExtractedField.value: string | number | null already assumes.


def _suggested_values(issues: list[ValidationIssue]) -> dict[str, Any]:
    """AC-02.3: a failed rule's suggested_value applies to every field it names."""
    suggestions: dict[str, Any] = {}
    for issue in issues:
        if issue.passed or issue.suggested_value is None:
            continue
        for field_name in issue.fields:
            suggestions[field_name] = issue.suggested_value
    return suggestions


def _field_detail(field: ExtractedField, suggested: dict[str, Any]) -> FieldDetailDto:
    return FieldDetailDto(
        name=field.name,
        value=_scalar_to_wire(field.value),
        confidence=field.confidence,
        page=field.page,
        region=None
        if field.region is None
        else RegionDto(page=field.region.page, polygon=list(field.region.polygon)),
        source=field.source.value,
        needs_review=field.needs_review,
        suggested_value=_scalar_to_wire(suggested.get(field.name)),
    )


@dataclass
class _StoredDetail:
    """What GET /results/{invoice_id} and its /file sibling need, kept alongside the
    summary DTO every _process() call already produces — not derivable from
    ProcessingResultDto alone, which only carries failed-issue messages, not full field
    detail or bounding regions. pdf_bytes stays process-local (no Blob Storage adapter
    exists yet, T-048) even though the Invoice/Vendor it came from are now durably in
    Cosmos — a restart loses the file, not the record that it was ever processed."""

    invoice: Invoice
    issues: list[ValidationIssue]
    pdf_bytes: bytes | None


def _process(
    invoice: Invoice,
    vendor: Vendor,
    adapter: MockPaymentInstructionAdapter,
    duplicate_lookup: DuplicateLookup,
    *,
    source_filename: str | None = None,
    extraction_method: str = "manual_entry",
    pdf_bytes: bytes | None = None,
) -> tuple[ProcessingResultDto, _StoredDetail]:
    """Pure pipeline: validate -> match -> route -> payment hand-off. Doesn't persist
    anything itself — BR-04's duplicate check needs the invoice *not* saved yet (a save
    happens right after this returns, in _ProcessingStore._process_and_persist), or a
    resubmission would match itself instead of a prior invoice.
    """
    ctx = ValidationContext(
        invoice=invoice,
        vendor_resolution=VendorResolution(vendor=vendor, method="exact_tax_id", score=100.0),
        duplicate_lookup=duplicate_lookup,
        today=_TODAY,
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

    dto = ProcessingResultDto(
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
        due_date=invoice.due_date,
    )
    detail = _StoredDetail(invoice=invoice, issues=issues, pdf_bytes=pdf_bytes)
    return dto, detail


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


def _resolve_or_create_vendor(
    vendor_repo: VendorRepository, name: str | None, tax_id: str | None
) -> Vendor:
    """Real vendor resolution (replaces the in-memory _VendorRegistry this router used
    before T-047 landed). Cosmos's vendor_tax_ids lookup is tax-id-keyed only — BR-11/
    BR-12's fuzzy name matching is core.matching.vendor's job, not this router's — so an
    extracted invoice with no tax ID genuinely can't be matched against a prior vendor
    here and always creates a new one. `bank_account` starts as None: FR-085 forbids ever
    sourcing it from the invoice itself, so a freshly-created vendor's first several
    payment instructions will show no bank reference — that gap is honest, not a bug to
    paper over.
    """
    if tax_id:
        existing = vendor_repo.find_by_tax_id(tax_id)
        if existing is not None:
            existing.invoices_processed += 1
            vendor_repo.save(existing)
            return existing
    vendor = Vendor(
        vendor_id=f"v-{uuid4().hex[:8]}",
        name=name or "Unknown Vendor",
        normalised_name=(name or "unknown vendor").strip().lower(),
        tax_id=tax_id,
        bank_account=None,
        requires_po=False,
        invoices_processed=0,
    )
    try:
        vendor_repo.save(vendor)
    except ValueError:
        # The race find_by_tax_id() above can't see: another concurrent call for the
        # same tax_id (again, the same-vendor-in-two-files batch case) claimed it
        # between our lookup and this save — CosmosVendorRepository.save() raises
        # ValueError once it's lost that race. Whoever won is the real vendor; use them
        # instead of crashing this file's whole extraction over a lookup that was only
        # ever going to be a fresh vendor anyway.
        if tax_id:
            winner = vendor_repo.find_by_tax_id(tax_id)
            if winner is not None:
                winner.invoices_processed += 1
                vendor_repo.save(winner)
                return winner
        raise
    return vendor


def _vendor_id_for_submission(
    vendor_repo: VendorRepository, tax_id: str | None, invoice_id: str
) -> str:
    """POST /process ("test scenario" entry) always builds a *fresh* Vendor from
    whatever the caller specified (bank_account, requires_po, invoices_processed) —
    unlike _resolve_or_create_vendor above, it never reuses an existing vendor's own
    field values. But it still needs the *id* of an existing vendor sharing this tax_id,
    if one exists (e.g. resubmitting against the seeded demo vendor's GSTIN) — Cosmos
    enforces one tax_id -> one vendor_id forever, so inventing a fresh id for an
    already-claimed tax_id raises on save(), and a random id per call would raise on the
    *second* call even for a vendor nothing else has touched. Reusing the right id turns
    a repeat submission into an upsert of that one vendor's properties instead.
    """
    if tax_id:
        existing = vendor_repo.find_by_tax_id(tax_id)
        return existing.vendor_id if existing is not None else f"v-upload-{tax_id}"
    return f"v-upload-{invoice_id}"


@dataclass
class _Repos:
    """Bundles the five Cosmos-backed dependencies every processing route needs, so route
    signatures don't repeat all five every time — see _get_repos below."""

    vendors: VendorRepository
    duplicates: DuplicateLookup
    invoices: InvoiceRepository
    audit_log: AuditLogWriter


def _get_repos(
    vendors: Annotated[VendorRepository, Depends(get_vendor_repo)],
    duplicates: Annotated[DuplicateLookup, Depends(get_duplicate_lookup)],
    invoices: Annotated[InvoiceRepository, Depends(get_invoice_repo)],
    audit_log: Annotated[AuditLogWriter, Depends(get_audit_log_writer)],
) -> _Repos:
    return _Repos(vendors=vendors, duplicates=duplicates, invoices=invoices, audit_log=audit_log)


Repos = Annotated[_Repos, Depends(_get_repos)]


class _ProcessingStore:
    """Everything processed in this process's lifetime — the two seeded demo invoices,
    plus every POST /process and POST /extract submission after them. The *view* here —
    this list, in this order — is still process-local and resets on restart, matching
    this being a dashboard, not a queryable API. What's no longer in-memory: every Vendor
    and Invoice behind that view is genuinely persisted to Cosmos DB (T-047) as it's
    processed, so BR-04's duplicate check and a vendor's real processing count survive a
    restart even though this list's ordering doesn't. Computed lazily so the first
    GET /results (not module import) is what seeds the demo invoices — matters for test
    isolation, see test_processing_api.py."""

    def __init__(self) -> None:
        self._results: list[ProcessingResultDto] | None = None
        self._detail: dict[str, _StoredDetail] = {}
        self._adapter = MockPaymentInstructionAdapter()

    def _process_and_persist(
        self,
        invoice: Invoice,
        vendor: Vendor,
        repos: _Repos,
        *,
        source_filename: str | None = None,
        extraction_method: str = "manual_entry",
        pdf_bytes: bytes | None = None,
    ) -> ProcessingResultDto:
        dto, detail = _process(
            invoice,
            vendor,
            self._adapter,
            repos.duplicates,
            source_filename=source_filename,
            extraction_method=extraction_method,
            pdf_bytes=pdf_bytes,
        )
        # BR-04 already caught it (the prior invoice was saved before this one's
        # duplicate check ran) — nothing new to persist, and trying anyway would hit
        # Cosmos's own unique key policy on invoice_number for no reason. dto already
        # correctly reads decision="exception" with the BR-04 issue attached; just skip
        # the write.
        already_flagged_as_duplicate = any(
            issue.rule_id == "BR-04" and not issue.passed for issue in detail.issues
        )
        if not already_flagged_as_duplicate:
            try:
                repos.invoices.save(invoice)
            except CosmosResourceExistsError as exc:
                # The race BR-04 *can't* catch: two concurrent calls for the same
                # (vendor, invoice_number) — e.g. the same file twice in one
                # POST /extract/batch — both ran their duplicate check before either had
                # saved, so both saw "no duplicate." Cosmos's unique key policy is what
                # actually catches the second write here; this turns that into a clean
                # error instead of a 500, since dto's own "not a duplicate" verdict is
                # now known to be wrong.
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"invoice {invoice.invoice_number!r} for this vendor was saved "
                        "by a concurrent request just before this one — treat it as a "
                        "duplicate."
                    ),
                ) from exc
        repos.audit_log.record(
            entity_type="invoice",
            entity_id=invoice.invoice_id,
            action="routed",
            actor="system",
            actor_role="pipeline",
            before=None,
            after={"decision": dto.decision, "reason": dto.reason},
        )
        self._detail[dto.invoice_id] = detail
        return dto

    def _ensure_seeded(self, repos: _Repos) -> list[ProcessingResultDto]:
        if self._results is None:
            vendor = _demo_vendor()
            repos.vendors.save(vendor)
            self._results = [
                self._process_and_persist(_clean_invoice(), vendor, repos),
                self._process_and_persist(_flagged_invoice(), vendor, repos),
            ]
        return self._results

    def list_results(self, repos: _Repos) -> list[ProcessingResultDto]:
        return self._ensure_seeded(repos)

    def accept_forwarded(self, invoice_id: str, repos: _Repos) -> ProcessingResultDto:
        """An admin accepting an invoice AP forwarded (api/routers/admin.py) — the one
        place a payment instruction gets built for something whose original router
        decision was never straight_through. Caller (admin.py) is responsible for
        confirming the review outcome is actually FORWARDED first; this method just does
        the accept itself, unconditionally, once called.
        """
        self._ensure_seeded(repos)
        detail = self._detail.get(invoice_id)
        index = next(
            (i for i, r in enumerate(self._results or []) if r.invoice_id == invoice_id), None
        )
        if detail is None or index is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"no processed invoice {invoice_id!r}"
            )
        invoice = detail.invoice
        vendor = repos.vendors.get(invoice.vendor_id) if invoice.vendor_id else None
        if vendor is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"no vendor on file for invoice {invoice_id!r}",
            )
        dto = (self._results or [])[index]
        routing_result = RoutingResult(
            decision=RoutingDecision(dto.decision),
            reason=dto.reason,
            overall_confidence=dto.overall_confidence,
        )
        instruction = build_payment_instruction(
            invoice, vendor, routing_result, approved_by="admin", now=_DEMO_NOW, human_approved=True
        )
        self._adapter.hand_off(instruction)
        updated = dto.model_copy(
            update={
                "payment_instruction": PaymentInstructionDto(
                    invoice_reference=instruction.invoice_reference,
                    vendor_reference=instruction.vendor_reference,
                    vendor_bank_account=instruction.vendor_bank_account,
                    amount=instruction.amount,
                    currency=instruction.currency,
                    due_date=instruction.due_date,
                    approval_evidence=instruction.approval_evidence,
                ),
                # Set explicitly, not left to whatever _with_review_outcome last cached
                # on this dto (possibly stale — nothing guarantees /results was called
                # again between AP's forward and this accept) — accept_forwarded's whole
                # job is this transition, so it can't depend on a side-effect elsewhere
                # having already made it true.
                "review_outcome": ReviewOutcome.APPROVED.value,
            }
        )
        if self._results is None:  # pragma: no cover - _ensure_seeded above guarantees this
            raise RuntimeError("_results unset despite _ensure_seeded — should be unreachable")
        self._results[index] = updated
        return updated

    def get_detail(self, invoice_id: str, repos: _Repos) -> InvoiceDetailDto:
        self._ensure_seeded(repos)
        detail = self._detail.get(invoice_id)
        if detail is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"no processed invoice {invoice_id!r}",
            )
        suggested = _suggested_values(detail.issues)
        return InvoiceDetailDto(
            invoice_id=detail.invoice.invoice_id,
            document_id=detail.invoice.document_id,
            vendor_name=detail.invoice.vendor_name,
            invoice_number=detail.invoice.invoice_number,
            pdf_url=f"/api/v1/processing/results/{invoice_id}/file"
            if detail.pdf_bytes is not None
            else None,
            fields=[_field_detail(f, suggested) for f in detail.invoice.fields.values()],
            issues=[
                ValidationIssueDetailDto(
                    rule_id=i.rule_id,
                    severity=i.severity.value,
                    passed=i.passed,
                    message=i.message,
                    fields=list(i.fields),
                )
                for i in detail.issues
                if not i.passed
            ],
        )

    def get_pdf_bytes(self, invoice_id: str, repos: _Repos) -> bytes:
        self._ensure_seeded(repos)
        detail = self._detail.get(invoice_id)
        if detail is None or detail.pdf_bytes is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"no file stored for invoice {invoice_id!r}",
            )
        return detail.pdf_bytes

    def delete(self, invoice_id: str, repos: _Repos, invoice_admin: InvoiceAdmin) -> None:
        self._ensure_seeded(repos)
        if self._results is not None:
            self._results = [r for r in self._results if r.invoice_id != invoice_id]
        self._detail.pop(invoice_id, None)
        invoice_admin.delete(invoice_id)

    def submit(self, request: ProcessInvoiceRequest, repos: _Repos) -> ProcessingResultDto:
        results = self._ensure_seeded(repos)
        invoice_id = f"inv-upload-{uuid4().hex[:8]}"
        invoice = _invoice_from_request(request, invoice_id)
        vendor = Vendor(
            vendor_id=_vendor_id_for_submission(repos.vendors, request.vendor_tax_id, invoice_id),
            name=request.vendor_name,
            normalised_name=request.vendor_name.lower(),
            tax_id=request.vendor_tax_id,
            bank_account=request.vendor_bank_account,
            requires_po=request.vendor_requires_po,
            invoices_processed=request.vendor_invoices_processed,
        )
        invoice.vendor_id = vendor.vendor_id
        repos.vendors.save(vendor)
        result = self._process_and_persist(
            invoice, vendor, repos, source_filename=request.source_filename
        )
        results.append(result)
        return result

    def extract_and_process(
        self,
        extractor: DocumentIntelligenceExtractor,
        content: bytes,
        filename: str,
        repos: _Repos,
        *,
        content_hash: str | None = None,
        page_count: int | None = None,
    ) -> ProcessingResultDto:
        started = time.monotonic()
        # FastAPIInstrumentor already opens a span per request (api/main.py) when
        # telemetry is enabled; get_current_span() is a real no-op with nothing
        # configured, so this is safe to call unconditionally rather than branching on
        # whether Azure Monitor happens to be wired up.
        span = trace.get_current_span()
        span.set_attribute("document_type", "invoice")
        results = self._ensure_seeded(repos)
        document_id = f"upload-{uuid4().hex[:8]}"
        # A caller that already knows this content's identity (e.g. preprocessing.py,
        # hashing the pre-optimization bytes /check reported) passes it explicitly —
        # content may be an optimized/rasterized transform of what was actually hashed,
        # so re-hashing it here would mint a second identity for the same document and
        # the cache could never match what /check told the user.
        content_hash = content_hash or hashlib.sha256(content).hexdigest()
        # Same reasoning for page_count: preprocessing.py already parsed this file once
        # and knows its real page count; recompute only when nobody handed us one (the
        # plain POST /extract path). Used for cost/metrics accounting, not billing itself
        # — Document Intelligence counts its own pages regardless of what we tell it.
        page_count = page_count if page_count is not None else count_pages(content, filename)

        # T-044/Article XII: never bill Document Intelligence twice for byte-identical
        # content. Real wall-clock time here, deliberately not _DEMO_NOW/BR-06's frozen
        # "today" a few lines below — those exist to make *validation* reproducible
        # against a fixed reference date; cache aging is about real elapsed time since a
        # real previous run, a different clock for a different purpose.
        cached = repos.invoices.get_by_content_hash(content_hash)
        decision = decide_cache_reuse(
            cached,
            cached_at=cached.extracted_at.date() if cached and cached.extracted_at else None,
            today=date.today(),
        )

        if decision.reuse and decision.cached_invoice is not None:
            source = decision.cached_invoice
            invoice = Invoice(
                invoice_id=f"upload-{uuid4().hex[:8]}",  # still a distinct processing
                # event — same content, but this is a genuinely new request that must go
                # through validate/match/route fresh (a vendor's history, e.g., may have
                # changed since the cached run).
                document_id=document_id,
                fields=dict(source.fields),
                lines=list(source.lines),
                model_id=source.model_id,
                model_version=source.model_version,
                extracted_at=source.extracted_at,
                content_hash=content_hash,
            )
            extraction_method = "cache_reuse"
        else:
            request = ExtractionRequest(
                document_id=document_id,
                blob_uri=f"local-upload://{filename}",  # see ExtractionRequest.content
                content_hash=content_hash,
                page_count=page_count,
                content=content,
            )
            try:
                extraction = extractor.extract(request)
            except TransientExtractionError as exc:
                metrics.record_pipeline_run(
                    outcome="error",
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    latency_seconds=time.monotonic() - started,
                )
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=f"Document Intelligence is temporarily unavailable: {exc}",
                ) from exc
            except PermanentExtractionError as exc:
                metrics.record_pipeline_run(
                    outcome="error",
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    latency_seconds=time.monotonic() - started,
                )
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=f"Document Intelligence could not read this file: {exc}",
                ) from exc
            invoice = extraction.invoice
            invoice.extracted_at = datetime.now(UTC).replace(tzinfo=None)
            invoice.content_hash = content_hash
            extraction_method = "azure_document_intelligence"

        metrics.record_extraction(method=extraction_method, page_count=page_count)

        vendor = _resolve_or_create_vendor(
            repos.vendors, invoice.vendor_name, invoice.vendor_tax_id
        )
        invoice.vendor_id = vendor.vendor_id
        result = self._process_and_persist(
            invoice,
            vendor,
            repos,
            source_filename=filename,
            extraction_method=extraction_method,
            pdf_bytes=content,
        )
        results.append(result)

        hitl_triggered = result.decision != "straight_through"
        span.set_attribute("model_id", invoice.model_id or "unknown")
        span.set_attribute("hitl_flag", hitl_triggered)
        metrics.record_pipeline_run(
            outcome="success",
            status_code=status.HTTP_200_OK,
            latency_seconds=time.monotonic() - started,
            page_count=page_count,
            field_completion_rate=_field_completion_rate(invoice),
            overall_confidence=result.overall_confidence,
            hitl_triggered=hitl_triggered,
            model_id=invoice.model_id,
        )
        return result

    def extract_batch(
        self,
        extractor: DocumentIntelligenceExtractor,
        files: list[tuple[str, bytes]],
        repos: _Repos,
    ) -> list[BatchFileResultDto]:
        """Fans extract_and_process() out across a bounded thread pool — each file still
        goes through the exact same real-OCR pipeline, one call per file, just concurrent
        instead of sequential. Seeding happens once, up front, outside the pool: every
        worker thread calls _ensure_seeded() too (extract_and_process does, internally),
        and two threads racing the None-check inside it before either has assigned
        self._results would seed twice. Doing it here first makes every worker's own call
        a no-op read of an already-populated list — no lock needed for that part.

        list.append() and dict.__setitem__() (self._detail, inside _process_and_persist)
        are individually atomic under the GIL, so concurrent workers writing into the same
        _results list / _detail dict don't corrupt either — they just land in whatever
        order threads happen to finish, which is fine for a dashboard list.
        """
        self._ensure_seeded(repos)

        def _process_one(filename: str, content: bytes) -> BatchFileResultDto:
            for attempt in range(2):
                try:
                    result = self.extract_and_process(extractor, content, filename, repos)
                    return BatchFileResultDto(filename=filename, status="success", result=result)
                except HTTPException as exc:
                    if exc.status_code == status.HTTP_503_SERVICE_UNAVAILABLE and attempt == 0:
                        time.sleep(_BATCH_RETRY_DELAY_SECONDS)
                        continue
                    return BatchFileResultDto(
                        filename=filename, status="error", error=str(exc.detail)
                    )
            raise AssertionError("unreachable")  # loop always returns or continues once

        results: list[BatchFileResultDto] = []
        with ThreadPoolExecutor(max_workers=_BATCH_MAX_WORKERS) as pool:
            futures = {pool.submit(_process_one, name, content): name for name, content in files}
            for future in as_completed(futures):
                results.append(future.result())
        return results


_store = _ProcessingStore()

ReviewRepo = Annotated[ReviewAssignmentRepository, Depends(get_review_repo)]
InvoiceAdminRepo = Annotated[InvoiceAdmin, Depends(get_invoice_admin)]


def _with_review_outcome(
    results: list[ProcessingResultDto], repo: ReviewAssignmentRepository
) -> list[ProcessingResultDto]:
    for result in results:
        assignment = repo.get(result.invoice_id)
        result.review_outcome = (
            assignment.outcome.value if assignment and assignment.outcome else None
        )
    return results


def _all_results(
    review_repo: ReviewAssignmentRepository, repos: _Repos
) -> list[ProcessingResultDto]:
    return _with_review_outcome(_store.list_results(repos), review_repo)


def _is_accepted(result: ProcessingResultDto) -> bool:
    """Straight-through (the router's own decision) or a human's later "approved" review
    — either way, a payment instruction exists or would have, and nothing further is
    needed from anyone. Shown on the Accepted tab, not the main Processing Results list."""
    return result.decision == "straight_through" or result.review_outcome == "approved"


def _is_rejected(result: ProcessingResultDto) -> bool:
    """A human's "rejected" review outcome — the *only* rejected outcome this app
    actually produces; RoutingDecision.REJECTED is defined but never returned by route()
    itself. Excluded from GET /results entirely — only GET /results/rejected (AP-gated)
    exposes these."""
    return result.review_outcome == "rejected"


def _is_due_soon(result: ProcessingResultDto, today: date) -> bool:
    return result.due_date is not None and (result.due_date - today).days <= _URGENT_DUE_WINDOW_DAYS


def _is_urgent(result: ProcessingResultDto, today: date) -> bool:
    """Both conditions the product asked for: a hard validation failure (EXCEPTION — the
    router already computes this, nothing new to infer) OR a due date within the window,
    whichever fires first. Not mutually exclusive with _is_due_soon — an invoice can be
    both, and that overlap is meaningful, not a bug to dedupe away."""
    return result.decision == "exception" or _is_due_soon(result, today)


class ProcessingSummaryDto(BaseModel):
    """Aggregate counts only — computed across every processed invoice, rejected ones
    included, unlike GET /results. A count is not the invoice's data; publishing "3
    rejected this week" is not the same disclosure as publishing which three."""

    total: int
    accepted_count: int
    rejected_count: int
    pending_count: int
    urgent_count: int
    due_soon_count: int


def _build_summary(results: list[ProcessingResultDto]) -> ProcessingSummaryDto:
    accepted = sum(1 for r in results if _is_accepted(r))
    rejected = sum(1 for r in results if _is_rejected(r))
    # Urgent/due-soon only mean something for an invoice someone still has to act on.
    # decision == "exception" reflects the *router's* original call, not whether a human
    # has since resolved it — without this filter, an invoice a reviewer already approved
    # or rejected keeps counting as urgent forever, which defeats the point of the number.
    pending = [r for r in results if not _is_accepted(r) and not _is_rejected(r)]
    urgent = sum(1 for r in pending if _is_urgent(r, _TODAY))
    due_soon = sum(1 for r in pending if _is_due_soon(r, _TODAY))
    return ProcessingSummaryDto(
        total=len(results),
        accepted_count=accepted,
        rejected_count=rejected,
        pending_count=len(pending),
        urgent_count=urgent,
        due_soon_count=due_soon,
    )


@router.get("/results", response_model=list[ProcessingResultDto])
def get_results(review_repo: ReviewRepo, repos: Repos) -> list[ProcessingResultDto]:
    """Never includes a rejected invoice's details (see _is_rejected) — those are only
    reachable through GET /results/rejected. Both endpoints require a session (this
    router's own dependencies=[Depends(get_session)]) — the split isn't about who can see
    it, only about keeping the main results list free of a terminal, no-longer-actionable
    state."""
    return [r for r in _all_results(review_repo, repos) if not _is_rejected(r)]


@router.get("/results/rejected", response_model=list[ProcessingResultDto])
def get_rejected_results(review_repo: ReviewRepo, repos: Repos) -> list[ProcessingResultDto]:
    return [r for r in _all_results(review_repo, repos) if _is_rejected(r)]


@router.get("/summary", response_model=ProcessingSummaryDto)
def get_summary(review_repo: ReviewRepo, repos: Repos) -> ProcessingSummaryDto:
    return _build_summary(_all_results(review_repo, repos))


@router.get("/results/{invoice_id}", response_model=InvoiceDetailDto)
def get_result_detail(invoice_id: str, repos: Repos) -> InvoiceDetailDto:
    """Backs the Review page's "Review this invoice" flow — replaces
    ReviewPage.tsx's hardcoded mockInvoice with whatever this process actually processed,
    seeded demo invoices included."""
    return _store.get_detail(invoice_id, repos)


@router.get("/results/{invoice_id}/file")
def get_result_file(invoice_id: str, repos: Repos) -> Response:
    """The raw bytes behind a real POST /extract upload, so the Review page's PDF viewer
    has something to render. 404s for anything without a stored file (manual entry,
    seeded demo invoices) — InvoiceDetailDto.pdf_url is None in that case, so the frontend
    never calls this without a real file to ask for."""
    return Response(content=_store.get_pdf_bytes(invoice_id, repos), media_type="application/pdf")


@router.delete("/results/{invoice_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_result(invoice_id: str, repos: Repos, invoice_admin: InvoiceAdminRepo) -> Response:
    """Removes an invoice from this process's results and from Cosmos — e.g. a duplicate
    the user wants gone for good, not just off the dashboard (leaving it in Cosmos would
    mean BR-04 keeps rejecting a resubmission of the same invoice number). Idempotent:
    deleting an id that isn't there is still a 204. Doesn't touch the review-assignment
    store; an orphaned claim record for a deleted invoice_id is harmless."""
    _store.delete(invoice_id, repos, invoice_admin)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/process", response_model=ProcessingResultDto)
def process_invoice(request: ProcessInvoiceRequest, repos: Repos) -> ProcessingResultDto:
    return _store.submit(request, repos)


Extractor = Annotated[DocumentIntelligenceExtractor, Depends(get_document_intelligence_extractor)]


@router.post("/extract", response_model=ProcessingResultDto)
async def extract_invoice(
    extractor: Extractor,
    repos: Repos,
    file: Annotated[UploadFile, File()],
    username: Annotated[str, Depends(get_current_username)],
) -> ProcessingResultDto:
    """Real OCR: sends the uploaded file's bytes to Azure Document Intelligence's
    prebuilt-invoice model and runs whatever it extracts through the same validate ->
    match -> route -> payment-instruction pipeline as everything else on this router.
    Every field on the result genuinely came from the document — nothing here is typed in.

    Counts against the signed-in user's demo document quota (api/auth.py) — checked
    before the call, so a request that would blow the budget never reaches Document
    Intelligence at all; recorded only after extract_and_process genuinely returns
    (an exception here means it never counts).
    """
    ensure_quota_available(username, needed=1)
    content = await file.read()
    result = _store.extract_and_process(extractor, content, file.filename or "upload.pdf", repos)
    record_documents_used(username, 1)
    return result


@router.post("/extract/batch", response_model=list[BatchFileResultDto])
async def extract_invoices_batch(
    extractor: Extractor,
    repos: Repos,
    files: Annotated[list[UploadFile], File()],
    username: Annotated[str, Depends(get_current_username)],
) -> list[BatchFileResultDto]:
    """Same real-OCR pipeline as POST /extract, fanned out across a bounded worker pool
    (_BATCH_MAX_WORKERS) instead of one file per request. One file failing — throttled,
    unreadable, whatever — doesn't sink the batch; it gets its own "error" entry while
    the rest still process.

    Quota (api/auth.py) is checked against the whole batch size up front — a batch that
    would exceed it is refused entirely rather than run partway — and only files that
    actually succeeded count against it afterward.
    """
    contents = [(f.filename or "upload.pdf", await f.read()) for f in files]
    ensure_quota_available(username, needed=len(contents))
    results = _store.extract_batch(extractor, contents, repos)
    record_documents_used(username, sum(1 for r in results if r.status == "success"))
    return results
