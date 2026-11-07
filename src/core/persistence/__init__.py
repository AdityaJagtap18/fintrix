"""Repository protocols only — no implementations here (Article I). Concrete SQL/Blob
backends live in `src/infrastructure` (Phase 2) and satisfy these structurally; nothing in
`core` imports them.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Protocol

from core.models import Invoice, PurchaseOrder, ReviewAssignment, Vendor


class VendorLookup(Protocol):
    """What `matching.vendor.resolve_vendor` (BR-11/BR-12) needs — exact tax-id lookup
    plus a candidate pool for fuzzy name matching."""

    def find_by_tax_id(self, tax_id: str) -> Vendor | None: ...

    def active_vendors(self) -> Iterable[Vendor]:
        """Candidate pool for fuzzy name matching. A real implementation should pre-filter
        (e.g. by trigram index) rather than return the entire vendor master, but the
        interface makes no assumption about scale."""
        ...


class DuplicateLookup(Protocol):
    """What BR-04 (exact duplicate) and BR-05 (near-duplicate) need."""

    def exact_duplicate_exists(
        self, vendor_tax_id: str, invoice_number: str, exclude_invoice_id: str | None = None
    ) -> bool: ...

    def find_near_duplicates(
        self, vendor_id: str, amount: Decimal, invoice_date: date, window_days: int
    ) -> list[str]:
        """Returns invoice_ids of prior invoices matching on vendor + amount within
        `window_days` of `invoice_date`, for AC-04.2's "prior document is linked"."""
        ...


class PurchaseOrderLookup(Protocol):
    """What `matching.three_way` (FR-040) needs to resolve a PO."""

    def find_by_number(self, po_number: str) -> PurchaseOrder | None: ...

    def find_by_vendor_and_amount(
        self, vendor_id: str, amount: Decimal, tolerance_pct: Decimal
    ) -> PurchaseOrder | None:
        """Fallback resolution when no PO number was extracted (FR-040)."""
        ...


class VendorRepository(VendorLookup, Protocol):
    def get(self, vendor_id: str) -> Vendor | None: ...

    def save(self, vendor: Vendor) -> None: ...


class InvoiceRepository(Protocol):
    def get(self, invoice_id: str) -> Invoice | None: ...

    def save(self, invoice: Invoice) -> None: ...

    def get_by_content_hash(self, content_hash: str) -> Invoice | None:
        """Backs FR-003: reuse a stored extraction result instead of re-extracting."""
        ...


class InvoiceAdmin(Protocol):
    """Operator cleanup (e.g. removing a duplicate from the dashboard) — not something
    core's validate/match/route logic ever calls, hence separate from InvoiceRepository
    rather than one more method bolted onto it."""

    def delete(self, invoice_id: str) -> None: ...


class AuditLogWriter(Protocol):
    """Append-only by contract (Article VIII) — no update/delete method exists on this
    interface on purpose."""

    def record(
        self,
        *,
        entity_type: str,
        entity_id: str,
        action: str,
        actor: str,
        actor_role: str,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        reason: str | None = None,
    ) -> None: ...


class ReviewAssignmentRepository(Protocol):
    """What `core.review.assignment` (US-03, FR-061/062) needs."""

    def get(self, invoice_id: str) -> ReviewAssignment | None: ...

    def save(self, assignment: ReviewAssignment) -> None: ...

    def find_expired(self, now: datetime) -> list[ReviewAssignment]:
        """Backs the lock-expiry sweep (FR-062, AC-03.2): every claim whose `expires_at`
        has passed with no submission yet."""
        ...
