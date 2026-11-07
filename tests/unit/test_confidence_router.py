from decimal import Decimal

from core.models import MatchResult, MatchStatus, RoutingDecision, Severity, ValidationIssue, Vendor
from core.routing.confidence_router import RoutingPolicy, route
from tests.unit.factories import make_invoice, make_line

PASSING_ISSUES: list[ValidationIssue] = [
    ValidationIssue(rule_id="BR-01", severity=Severity.HARD, passed=True, message="ok"),
]
MATCHED = MatchResult(status=MatchStatus.MATCHED, message="ok")


def established_vendor(**overrides) -> Vendor:
    defaults = dict(
        vendor_id="v-1",
        name="Acme Supplies",
        normalised_name="acme supplies",
        requires_po=True,
        invoices_processed=50,
    )
    defaults.update(overrides)
    return Vendor(**defaults)


def test_shadow_mode_default_is_enabled():
    assert RoutingPolicy().shadow_mode is True


def test_shadow_mode_records_computed_decision_but_returns_review():
    # T-031 done-when: with shadow mode on, a perfect invoice returns REVIEW while
    # shadow_decision reads STRAIGHT_THROUGH.
    invoice = make_invoice(confidence=0.99)
    policy = RoutingPolicy(shadow_mode=True)
    result = route(invoice, PASSING_ISSUES, MATCHED, established_vendor(), policy)
    assert result.decision == RoutingDecision.REVIEW
    assert result.shadow_decision == RoutingDecision.STRAIGHT_THROUGH
    assert result.shadow_reason == "confidence:auto"
    assert result.is_shadowed


# --- AC-01.1: clean, confident, matched invoice posts straight through ---
def test_ac_01_1_clean_invoice_goes_straight_through():
    invoice = make_invoice(confidence=0.99)
    policy = RoutingPolicy(shadow_mode=False)
    result = route(invoice, PASSING_ISSUES, MATCHED, established_vendor(), policy)
    assert result.decision == RoutingDecision.STRAIGHT_THROUGH
    assert result.reason == "confidence:auto"


# --- AC-01.3: a vendor never seen before is never posted straight through ---
def test_ac_01_3_new_vendor_never_goes_straight_through():
    invoice = make_invoice(confidence=0.999)
    new_vendor = established_vendor(invoices_processed=0)
    policy = RoutingPolicy(shadow_mode=False, min_vendor_history=1)
    result = route(invoice, PASSING_ISSUES, MATCHED, new_vendor, policy)
    assert result.decision != RoutingDecision.STRAIGHT_THROUGH
    assert result.reason == "policy:new_vendor"


def test_no_resolved_vendor_never_goes_straight_through():
    invoice = make_invoice(confidence=0.999)
    policy = RoutingPolicy(shadow_mode=False)
    result = route(invoice, PASSING_ISSUES, MATCHED, None, policy)
    assert result.decision != RoutingDecision.STRAIGHT_THROUGH
    assert result.reason == "policy:new_vendor"


# --- AC-04.1: duplicate invoice is blocked as EXCEPTION ---
def test_ac_04_1_duplicate_blocks_as_exception():
    invoice = make_invoice(confidence=0.99)
    issues = [
        ValidationIssue(rule_id="BR-04", severity=Severity.HARD, passed=False, message="duplicate")
    ]
    policy = RoutingPolicy(shadow_mode=False)
    result = route(invoice, issues, MATCHED, established_vendor(), policy)
    assert result.decision == RoutingDecision.EXCEPTION
    assert result.reason == "duplicate"


# --- AC-05.1: bank detail change blocks regardless of confidence ---
def test_ac_05_1_bank_detail_change_blocks_regardless_of_confidence():
    invoice = make_invoice(confidence=0.999)  # extraction confidence is irrelevant here
    issues = [
        ValidationIssue(
            rule_id="BR-13", severity=Severity.HARD, passed=False, message="bank mismatch"
        )
    ]
    policy = RoutingPolicy(shadow_mode=False)
    result = route(invoice, issues, MATCHED, established_vendor(), policy)
    assert result.decision == RoutingDecision.EXCEPTION
    assert result.reason == "validation:BR-13"


# --- AC-06.2: a 3-way match mismatch blocks as EXCEPTION for a PO-required vendor ---
def test_ac_06_2_match_mismatch_blocks_as_exception_when_po_required():
    invoice = make_invoice(confidence=0.99)
    mismatch = MatchResult(status=MatchStatus.MISMATCH, message="price variance")
    policy = RoutingPolicy(shadow_mode=False)
    result = route(invoice, PASSING_ISSUES, mismatch, established_vendor(requires_po=True), policy)
    assert result.decision == RoutingDecision.EXCEPTION
    assert result.reason == "three_way_match:mismatch"


def test_mismatch_does_not_block_when_vendor_does_not_require_po():
    invoice = make_invoice(confidence=0.99)
    mismatch = MatchResult(status=MatchStatus.MISMATCH, message="irrelevant")
    policy = RoutingPolicy(shadow_mode=False)
    result = route(invoice, PASSING_ISSUES, mismatch, established_vendor(requires_po=False), policy)
    assert result.decision == RoutingDecision.STRAIGHT_THROUGH


def test_partial_match_forces_review():
    invoice = make_invoice(confidence=0.99)
    partial = MatchResult(status=MatchStatus.PARTIAL, message="partially matched")
    policy = RoutingPolicy(shadow_mode=False)
    result = route(invoice, PASSING_ISSUES, partial, established_vendor(), policy)
    assert result.decision == RoutingDecision.REVIEW
    assert result.reason == "three_way_match:partial"


def test_amount_over_vendor_ceiling_forces_review():
    invoice = make_invoice(confidence=0.99, total=Decimal("100000.00"))
    vendor = established_vendor(max_auto_amount=Decimal("5000.00"))
    policy = RoutingPolicy(shadow_mode=False)
    result = route(invoice, PASSING_ISSUES, MATCHED, vendor, policy)
    assert result.decision == RoutingDecision.REVIEW
    assert result.reason == "policy:amount_ceiling"


def test_amount_over_the_global_default_ceiling_forces_review_with_no_vendor_override():
    invoice = make_invoice(confidence=0.99, total=Decimal("1000001.00"))
    vendor = established_vendor()  # no max_auto_amount of its own
    policy = RoutingPolicy(shadow_mode=False)  # policy.max_auto_amount defaults to 10L
    result = route(invoice, PASSING_ISSUES, MATCHED, vendor, policy)
    assert result.decision == RoutingDecision.REVIEW
    assert result.reason == "policy:amount_ceiling"


def test_amount_at_or_under_the_global_default_ceiling_is_unaffected():
    invoice = make_invoice(confidence=0.99, total=Decimal("1000000.00"))
    vendor = established_vendor()
    policy = RoutingPolicy(shadow_mode=False)
    result = route(invoice, PASSING_ISSUES, MATCHED, vendor, policy)
    assert result.decision == RoutingDecision.STRAIGHT_THROUGH


def test_a_vendors_own_higher_ceiling_overrides_the_global_default():
    invoice = make_invoice(confidence=0.99, total=Decimal("5000000.00"))
    vendor = established_vendor(max_auto_amount=Decimal("10000000.00"))  # explicitly higher
    policy = RoutingPolicy(shadow_mode=False)
    result = route(invoice, PASSING_ISSUES, MATCHED, vendor, policy)
    assert result.decision == RoutingDecision.STRAIGHT_THROUGH


def test_low_confidence_field_forces_review_even_above_auto_overall():
    # Critical fields (which drive `overall`) are all high-confidence; a non-critical
    # field (due_date) is low. That alone must be enough to block STP.
    invoice = make_invoice(confidence=0.99)
    from core.models import ExtractedField

    invoice.fields["due_date"] = ExtractedField(
        name="due_date", value=invoice.due_date, confidence=0.2
    )
    policy = RoutingPolicy(shadow_mode=False)
    result = route(invoice, PASSING_ISSUES, MATCHED, established_vendor(), policy)
    assert result.decision == RoutingDecision.REVIEW
    assert result.reason == "confidence:field_review"


def test_low_confidence_line_forces_review():
    invoice = make_invoice(confidence=0.99, lines=[make_line(1, row_confidence=0.3)])
    policy = RoutingPolicy(shadow_mode=False)
    result = route(invoice, PASSING_ISSUES, MATCHED, established_vendor(), policy)
    assert result.decision == RoutingDecision.REVIEW
    assert result.reason == "confidence:field_review"


def test_very_low_overall_confidence_is_full_review():
    invoice = make_invoice(confidence=0.10)
    policy = RoutingPolicy(shadow_mode=False)
    result = route(invoice, PASSING_ISSUES, MATCHED, established_vendor(), policy)
    assert result.decision == RoutingDecision.FULL_REVIEW


def test_human_corrected_field_never_counts_as_low_confidence():
    invoice = make_invoice(confidence=0.99)
    invoice.apply_correction("due_date", invoice.due_date)  # was low, now human-sourced -> 1.0
    invoice.fields["due_date"].confidence = 0.01  # even if stored confidence stays low
    policy = RoutingPolicy(shadow_mode=False)
    result = route(invoice, PASSING_ISSUES, MATCHED, established_vendor(), policy)
    assert result.decision == RoutingDecision.STRAIGHT_THROUGH


def test_vendor_threshold_override_takes_priority_over_policy_default():
    # Vendor requires a much higher bar than the global default.
    invoice = make_invoice(confidence=0.95)
    vendor = established_vendor(auto_threshold=0.999)
    policy = RoutingPolicy(shadow_mode=False, auto_threshold=0.80)
    result = route(invoice, PASSING_ISSUES, MATCHED, vendor, policy)
    assert result.decision != RoutingDecision.STRAIGHT_THROUGH
