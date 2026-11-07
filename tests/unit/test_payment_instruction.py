from datetime import date, datetime
from decimal import Decimal

import pytest

from core.models import RoutingDecision, RoutingResult
from core.payment.instruction import (
    AlreadyHandedOffError,
    MockPaymentInstructionAdapter,
    build_payment_instruction,
)
from tests.unit.factories import make_field, make_invoice
from tests.unit.test_confidence_router import established_vendor

NOW = datetime(2026, 8, 29, 12, 0, 0)
STP_RESULT = RoutingResult(
    decision=RoutingDecision.STRAIGHT_THROUGH, reason="confidence:auto", overall_confidence=0.99
)


def test_build_payment_instruction_uses_vendor_master_bank_account_not_invoice():
    # FR-085: the instruction must reference the vendor master's bank details, never a
    # value read off the invoice itself (that's exactly the payment-redirection-fraud
    # vector BR-13 exists to catch — the instruction must not trust the document either).
    vendor = established_vendor(bank_account="VENDOR-MASTER-ACCT-001")
    invoice = make_invoice(
        extra_fields={
            "remittance_bank_account": make_field(
                "remittance_bank_account", "ATTACKER-CONTROLLED-ACCT"
            )
        }
    )

    instruction = build_payment_instruction(
        invoice, vendor, STP_RESULT, approved_by="system", now=NOW
    )

    assert instruction.vendor_bank_account == "VENDOR-MASTER-ACCT-001"


def test_build_payment_instruction_contains_required_fr080_fields():
    vendor = established_vendor()
    invoice = make_invoice(
        invoice_id="inv-42",
        invoice_number="INV-1001",
        total=Decimal("1180.00"),
        amount_due=Decimal("1180.00"),
        currency="INR",
        due_date=date(2026, 8, 31),
    )

    instruction = build_payment_instruction(
        invoice, vendor, STP_RESULT, approved_by="system", now=NOW
    )

    assert instruction.invoice_reference == "INV-1001"
    assert instruction.vendor_reference == vendor.vendor_id
    assert instruction.amount == Decimal("1180.00")
    assert instruction.currency == "INR"
    assert instruction.due_date == date(2026, 8, 31)
    assert instruction.approval_evidence == "system:straight_through:confidence:auto"
    assert instruction.created_at == NOW


def test_build_payment_instruction_falls_back_to_total_when_amount_due_missing():
    vendor = established_vendor()
    invoice = make_invoice(total=Decimal("500.00"), amount_due=None)
    instruction = build_payment_instruction(
        invoice, vendor, STP_RESULT, approved_by="system", now=NOW
    )
    assert instruction.amount == Decimal("500.00")


def test_build_payment_instruction_rejects_a_non_straight_through_decision():
    # A payment instruction can only ever be built for an approved outcome — never
    # speculatively for an invoice still in review or exception (FR-080: "for every
    # approved invoice").
    vendor = established_vendor()
    invoice = make_invoice()
    review_result = RoutingResult(
        decision=RoutingDecision.REVIEW, reason="confidence:field_review", overall_confidence=0.5
    )
    with pytest.raises(ValueError, match="not approved"):
        build_payment_instruction(invoice, vendor, review_result, approved_by="system", now=NOW)


def test_build_payment_instruction_allows_a_non_straight_through_decision_when_human_approved():
    # An admin accepting an invoice AP forwarded (api/routers/admin.py) — the router's
    # own decision was never straight_through, but a real human explicitly approved it.
    vendor = established_vendor()
    invoice = make_invoice(
        invoice_number="INV-2001", total=Decimal("750.00"), amount_due=Decimal("750.00")
    )
    review_result = RoutingResult(
        decision=RoutingDecision.REVIEW, reason="confidence:field_review", overall_confidence=0.5
    )

    instruction = build_payment_instruction(
        invoice, vendor, review_result, approved_by="admin", now=NOW, human_approved=True
    )

    assert instruction.amount == Decimal("750.00")
    # The evidence trail still names the router's real original call, not a fabricated
    # straight-through — approved_by is what actually names the human override.
    assert instruction.approval_evidence == "admin:review:confidence:field_review"


def test_mock_adapter_records_the_instruction():
    vendor = established_vendor()
    invoice = make_invoice()
    instruction = build_payment_instruction(
        invoice, vendor, STP_RESULT, approved_by="system", now=NOW
    )

    adapter = MockPaymentInstructionAdapter()
    adapter.hand_off(instruction)

    assert adapter.get(invoice.invoice_id) == instruction


def test_mock_adapter_hand_off_is_idempotent_per_invoice():
    # FR-081: the same invoice must never be handed off twice, even on retry.
    vendor = established_vendor()
    invoice = make_invoice()
    instruction = build_payment_instruction(
        invoice, vendor, STP_RESULT, approved_by="system", now=NOW
    )

    adapter = MockPaymentInstructionAdapter()
    adapter.hand_off(instruction)
    adapter.hand_off(instruction)  # same instruction again — must not raise, must not duplicate

    assert adapter.hand_off_count(invoice.invoice_id) == 1


def test_mock_adapter_rejects_a_conflicting_second_instruction_for_the_same_invoice():
    vendor = established_vendor()
    invoice = make_invoice()
    first = build_payment_instruction(invoice, vendor, STP_RESULT, approved_by="system", now=NOW)
    second = build_payment_instruction(
        invoice, vendor, STP_RESULT, approved_by="system", now=NOW.replace(hour=13)
    )

    adapter = MockPaymentInstructionAdapter()
    adapter.hand_off(first)
    with pytest.raises(AlreadyHandedOffError):
        adapter.hand_off(second)
