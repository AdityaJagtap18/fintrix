"""Domain models: Invoice, ExtractedField, BoundingRegion, LineItem, ValidationIssue,
MatchResult, RoutingResult, and the enums that drive routing.

No Azure SDK import belongs in this file (Article I). Money is `Decimal`, never `float` —
a rounding error here is a wrong payment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any


class FieldSource(StrEnum):
    """Where a field's value came from. Drives FR-054: a human-corrected field is fully
    confident for routing purposes, regardless of what the model originally said."""

    MODEL = "model"
    HUMAN = "human"
    RULE = "rule"
    DEFAULT = "default"


class Severity(StrEnum):
    HARD = "hard"
    SOFT = "soft"


class MatchStatus(StrEnum):
    MATCHED = "matched"
    MISMATCH = "mismatch"
    PARTIAL = "partial"
    NOT_APPLICABLE = "not_applicable"


class RoutingDecision(StrEnum):
    STRAIGHT_THROUGH = "straight_through"
    REVIEW = "review"
    FULL_REVIEW = "full_review"
    EXCEPTION = "exception"
    REJECTED = "rejected"


class InvoiceState(StrEnum):
    """Mirrors spec.md §9. Terminal states: RELEASED, REJECTED, QUARANTINED."""

    RECEIVED = "received"
    CLASSIFIED = "classified"
    EXTRACTED = "extracted"
    VALIDATED = "validated"
    MATCHED = "matched"
    ROUTED = "routed"
    STRAIGHT_THROUGH = "straight_through"
    IN_REVIEW = "in_review"
    EXCEPTION = "exception"
    APPROVED = "approved"
    REJECTED = "rejected"
    HANDOFF = "handoff"
    HANDOFF_FAILED = "handoff_failed"
    RELEASED = "released"
    QUARANTINED = "quarantined"


TERMINAL_STATES = frozenset(
    {InvoiceState.RELEASED, InvoiceState.REJECTED, InvoiceState.QUARANTINED}
)


@dataclass(frozen=True, slots=True)
class BoundingRegion:
    """A polygon on a specific page, as returned by Document Intelligence
    `boundingRegions`. `polygon` is a flat sequence of (x, y) pairs in the unit the source
    API uses (inches for DI); the review UI draws it directly over the rendered page."""

    page: int
    polygon: tuple[float, ...]

    def __post_init__(self) -> None:
        if self.page < 1:
            raise ValueError("page numbers are 1-indexed")
        if len(self.polygon) % 2 != 0:
            raise ValueError("polygon must be an even number of (x, y) coordinates")


@dataclass(slots=True)
class ExtractedField:
    """One field with its provenance. `confidence` is the model's reported confidence;
    `effective_confidence` is what routing actually uses (FR-054)."""

    name: str
    value: Any
    confidence: float = 0.0
    page: int | None = None
    region: BoundingRegion | None = None
    source: FieldSource = FieldSource.MODEL
    needs_review: bool = False

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0.0, 1.0], got {self.confidence}")

    @property
    def effective_confidence(self) -> float:
        return 1.0 if self.source == FieldSource.HUMAN else self.confidence

    def corrected(self, human_value: Any) -> ExtractedField:
        """Return a new field reflecting a reviewer's correction. Confidence becomes 1.0
        via `effective_confidence`; the field is no longer flagged for review."""
        return ExtractedField(
            name=self.name,
            value=human_value,
            confidence=self.confidence,
            page=self.page,
            region=self.region,
            source=FieldSource.HUMAN,
            needs_review=False,
        )


@dataclass(slots=True)
class LineItem:
    line_number: int
    description: str = ""
    quantity: Decimal = Decimal("0")
    unit_price: Decimal = Decimal("0")
    amount: Decimal = Decimal("0")
    tax_rate: Decimal | None = None
    product_code: str | None = None
    row_confidence: float = 0.0
    matched_po_line_id: str | None = None


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """The outcome of one business rule (Section 8 of spec.md), always recorded —
    passing rules included (FR-030)."""

    rule_id: str
    severity: Severity
    passed: bool
    message: str
    fields: tuple[str, ...] = ()
    suggested_value: Any | None = None


@dataclass(frozen=True, slots=True)
class MatchVariance:
    """One discrepancy found during 3-way match, stated in both currency and percentage
    per AC-06.2/AC-06.3 so the reviewer never has to do arithmetic."""

    line_number: int | None
    kind: str  # "price" | "quantity" | "closed_po" | "not_found"
    expected: Decimal | None
    actual: Decimal | None
    difference: Decimal | None
    difference_pct: Decimal | None
    message: str = ""


@dataclass(frozen=True, slots=True)
class MatchResult:
    status: MatchStatus
    message: str = ""
    variances: tuple[MatchVariance, ...] = ()


@dataclass(frozen=True, slots=True)
class RoutingResult:
    decision: RoutingDecision
    reason: str
    overall_confidence: float
    shadow_decision: RoutingDecision | None = None
    shadow_reason: str | None = None

    @property
    def is_shadowed(self) -> bool:
        return self.shadow_decision is not None


@dataclass(slots=True)
class Vendor:
    vendor_id: str
    name: str
    normalised_name: str
    tax_id: str | None = None
    address: str | None = None
    bank_account: str | None = None
    currency: str | None = None
    requires_po: bool = True
    auto_threshold: float | None = None
    review_threshold: float | None = None
    max_auto_amount: Decimal | None = None
    invoices_processed: int = 0
    is_active: bool = True


@dataclass(frozen=True, slots=True)
class VendorResolution:
    """Outcome of resolving an extracted vendor name/tax-id to the vendor master
    (BR-11, BR-12), computed once in the pipeline's `resolve_vendor` stage and reused by
    both validation and routing."""

    vendor: Vendor | None
    method: str  # "exact_tax_id" | "fuzzy_name" | "none"
    score: float  # 0-100; 100 for an exact tax-id match


@dataclass(slots=True)
class PurchaseOrderLine:
    po_line_id: str
    po_number: str
    line_number: int
    product_code: str | None
    unit_price: Decimal
    quantity_ordered: Decimal
    quantity_received: Decimal
    quantity_invoiced: Decimal

    @property
    def quantity_received_unbilled(self) -> Decimal:
        return self.quantity_received - self.quantity_invoiced


@dataclass(slots=True)
class PurchaseOrder:
    po_number: str
    vendor_id: str
    is_closed: bool = False
    lines: list[PurchaseOrderLine] = field(default_factory=list)


@dataclass(slots=True)
class Invoice:
    """The aggregate the whole pipeline operates on. `fields` carries every extracted
    field by canonical name (FR-020); scalar convenience properties below read from it so
    callers don't have to know the storage shape."""

    invoice_id: str
    document_id: str
    fields: dict[str, ExtractedField] = field(default_factory=dict)
    lines: list[LineItem] = field(default_factory=list)
    vendor_id: str | None = None
    state: InvoiceState = InvoiceState.RECEIVED
    model_id: str | None = None
    model_version: str | None = None
    # T-044/Article XII: when this extraction actually ran, so
    # core.extraction.cache.decide_cache_reuse has something to age against the cache
    # window. None for anything built before this field existed, or never persisted.
    extracted_at: datetime | None = None
    # sha256 of the source file's raw bytes — what InvoiceRepository.get_by_content_hash
    # actually looks up by. None for anything not extracted from a real uploaded file
    # (manual entry, seeded demo data).
    content_hash: str | None = None

    def confidence(self, field_name: str) -> float:
        """Effective confidence for a field, honouring FR-054. Missing fields read as 0.0
        so an omitted required field can never accidentally look confident."""
        extracted = self.fields.get(field_name)
        return extracted.effective_confidence if extracted else 0.0

    def value(self, field_name: str) -> Any | None:
        extracted = self.fields.get(field_name)
        return extracted.value if extracted else None

    def apply_correction(self, field_name: str, human_value: Any) -> None:
        """AC-02.4: a reviewer's correction is written back with source=human so
        `confidence()` returns 1.0 for it from this point on."""
        existing = self.fields.get(field_name)
        if existing is None:
            self.fields[field_name] = ExtractedField(
                name=field_name, value=human_value, source=FieldSource.HUMAN
            )
        else:
            self.fields[field_name] = existing.corrected(human_value)

    # --- convenience accessors over commonly used canonical fields (FR-020) ---
    @property
    def vendor_name(self) -> str | None:
        return self.value("vendor_name")

    @property
    def vendor_tax_id(self) -> str | None:
        return self.value("vendor_tax_id")

    @property
    def invoice_number(self) -> str | None:
        return self.value("invoice_number")

    @property
    def invoice_date(self) -> date | None:
        return self.value("invoice_date")

    @property
    def due_date(self) -> date | None:
        return self.value("due_date")

    @property
    def po_number(self) -> str | None:
        return self.value("po_number")

    @property
    def currency(self) -> str | None:
        return self.value("currency")

    @property
    def subtotal(self) -> Decimal | None:
        return self.value("subtotal")

    @property
    def tax_total(self) -> Decimal | None:
        return self.value("tax_total")

    @property
    def total(self) -> Decimal | None:
        return self.value("total")

    @property
    def amount_due(self) -> Decimal | None:
        return self.value("amount_due")


class ReviewOutcome(StrEnum):
    # Set only by an admin accepting a previously-forwarded invoice (see api/auth.py's
    # AP/admin split) — never directly by AP anymore. Terminal.
    APPROVED = "approved"
    # Set by AP directly. Terminal — admin has no reject action.
    REJECTED = "rejected"
    # Set by AP: "I think this should be accepted, but I don't have final authority" —
    # sits in the admin queue (GET /api/v1/admin/forwarded) until an admin accepts it,
    # at which point the outcome becomes APPROVED. Not terminal.
    FORWARDED = "forwarded"


@dataclass(slots=True)
class ReviewAssignment:
    """The exclusive claim a reviewer holds on an invoice (US-03). A record with
    `claimed_by=None` — the state after `release()` or an expiry sweep — means the invoice
    is available in the queue."""

    invoice_id: str
    claimed_by: str | None = None
    claimed_at: datetime | None = None
    expires_at: datetime | None = None
    submitted_at: datetime | None = None
    outcome: ReviewOutcome | None = None

    @property
    def is_claimed(self) -> bool:
        return self.claimed_by is not None and self.submitted_at is None

    def is_expired(self, now: datetime) -> bool:
        return self.is_claimed and self.expires_at is not None and now >= self.expires_at
