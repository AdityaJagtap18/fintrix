"""Payment instruction hand-off (spec.md "Payment instruction hand-off", FR-080..085,
T-080). This system never moves money — see spec.md §5, "Why payment execution is
excluded": it emits a structured instruction and the customer's own treasury/ERP system
executes it. Nothing in this module can initiate, authorise, or execute a transfer, and it
never stores a banking credential (FR-084) — it only ever reads a vendor's *already-on-file*
bank reference out of the vendor master, never out of the invoice itself (FR-085), because
the invoice is exactly the untrusted input BR-13's fraud check exists for.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from core.models import Invoice, RoutingDecision, RoutingResult, Vendor


class PaymentInstructionError(Exception):
    """Base for every error this module can raise."""


class AlreadyHandedOffError(PaymentInstructionError):
    """FR-081: the same invoice must never be handed off twice, even on retry or replay."""

    def __init__(self, invoice_id: str):
        self.invoice_id = invoice_id
        super().__init__(f"invoice {invoice_id} has already been handed off for payment")


@dataclass(frozen=True, slots=True)
class PaymentInstruction:
    """FR-080's required content: vendor reference, amount, currency, due date, invoice
    reference, and approval evidence. `vendor_bank_account` backs FR-085 and is sourced
    exclusively from the vendor master (see `build_payment_instruction`), never from any
    field extracted off the invoice document."""

    invoice_id: str
    invoice_reference: str
    vendor_reference: str
    vendor_bank_account: str | None
    amount: Decimal | None
    currency: str | None
    due_date: date | None
    approval_evidence: str
    created_at: datetime


def build_payment_instruction(
    invoice: Invoice,
    vendor: Vendor,
    routing_result: RoutingResult,
    *,
    approved_by: str,
    now: datetime,
    human_approved: bool = False,
) -> PaymentInstruction:
    """Assemble a `PaymentInstruction` for an invoice that has actually been approved.

    Only ever called for a `STRAIGHT_THROUGH` (system-approved) routing_result, or for
    any other decision with `human_approved=True` — an admin accepting an invoice AP
    forwarded (api/routers/admin.py), say. `routing_result.decision` in that second case
    still reflects the router's *original* call (review/exception/...), not the human
    override; `human_approved` is the explicit signal that this is intentional, not a
    caller accidentally handing off an invoice nobody actually approved. Never called for
    a `shadow_mode` result, whose `decision` is always `REVIEW` regardless of what would
    have happened (`RoutingResult.shadow_decision` is the one to check before calling this
    in that case).
    """
    if routing_result.decision != RoutingDecision.STRAIGHT_THROUGH and not human_approved:
        raise ValueError(
            f"cannot build a payment instruction for a routing decision that is not "
            f"approved (STRAIGHT_THROUGH) unless human_approved=True: got "
            f"{routing_result.decision}"
        )

    amount = invoice.amount_due if invoice.amount_due is not None else invoice.total
    return PaymentInstruction(
        invoice_id=invoice.invoice_id,
        invoice_reference=invoice.invoice_number or invoice.invoice_id,
        vendor_reference=vendor.vendor_id,
        vendor_bank_account=vendor.bank_account,
        amount=amount,
        currency=invoice.currency,
        due_date=invoice.due_date,
        approval_evidence=f"{approved_by}:{routing_result.decision.value}:{routing_result.reason}",
        created_at=now,
    )


class PaymentInstructionAdapter(ABC):
    """One hand-off target — a blob drop today, a real ERP/treasury API tomorrow. Nothing
    upstream of this class needs to change to replace the target (NFR-051): swapping it is
    one new adapter, not a rewrite of the pipeline that calls it."""

    @abstractmethod
    def hand_off(self, instruction: PaymentInstruction) -> None:
        """Deliver `instruction` to the hand-off target. Implementations MUST be
        idempotent per `instruction.invoice_id` (FR-081)."""

    @abstractmethod
    def get(self, invoice_id: str) -> PaymentInstruction | None:
        """The instruction on file for `invoice_id`, if any — lets a caller confirm a
        hand-off actually landed without re-sending it."""


class MockPaymentInstructionAdapter(PaymentInstructionAdapter):
    """Dev/test placeholder — records instructions in memory instead of writing to blob.
    Real enough to prove the calling code's idempotency and field-mapping logic; state
    doesn't survive a restart. A real blob-writing adapter (Phase 2 infra) satisfies the
    same `PaymentInstructionAdapter` interface structurally; nothing above it changes."""

    def __init__(self) -> None:
        self._store: dict[str, PaymentInstruction] = {}

    def hand_off(self, instruction: PaymentInstruction) -> None:
        existing = self._store.get(instruction.invoice_id)
        if existing is not None:
            if existing != instruction:
                raise AlreadyHandedOffError(instruction.invoice_id)
            return  # identical repeat of an already-recorded hand-off — a no-op
        self._store[instruction.invoice_id] = instruction

    def get(self, invoice_id: str) -> PaymentInstruction | None:
        return self._store.get(invoice_id)

    def hand_off_count(self, invoice_id: str) -> int:
        """Test/inspection helper — 1 once `invoice_id` has a recorded instruction, 0
        otherwise. Not part of the `PaymentInstructionAdapter` interface; exists to make
        FR-081 ("never handed off twice") directly assertable without reaching into
        internals."""
        return 1 if invoice_id in self._store else 0
