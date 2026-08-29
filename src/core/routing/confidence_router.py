"""The confidence router (plan.md §5) — the single most commercially persuasive piece of
this system per README §3. `route()` is pure: no I/O, no clock reads beyond what's passed
in, fully deterministic given its inputs. That is what makes shadow mode possible — the
same call computes the real decision whether or not it is ever acted on.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.models import (
    Invoice,
    MatchResult,
    MatchStatus,
    RoutingDecision,
    RoutingResult,
    Severity,
    ValidationIssue,
    Vendor,
)

# FR-020: the fields critical enough that a low score on any one of them caps `overall`,
# and important-but-not-gating fields checked separately for the "no low field" condition.
CRITICAL_FIELDS: tuple[str, ...] = (
    "vendor_name",
    "invoice_number",
    "invoice_date",
    "total",
    "currency",
)
IMPORTANT_FIELDS: tuple[str, ...] = ("subtotal", "tax_total", "due_date", "po_number")


@dataclass(slots=True)
class RoutingPolicy:
    """Global defaults. Per-vendor overrides live on `Vendor.auto_threshold` /
    `Vendor.review_threshold` / `Vendor.max_auto_amount` and win when set (plan.md §5:
    "vendor override → global config → code default").

    `shadow_mode` defaults to `True` — automation is always an explicit, audited choice,
    never a deployment default (README §3, FR-056/057).
    """

    auto_threshold: float = 0.92
    review_threshold: float = 0.70
    line_threshold: float = 0.80
    min_vendor_history: int = 1
    shadow_mode: bool = True


def _resolve_threshold(
    vendor: Vendor | None, vendor_value: float | None, policy_value: float
) -> float:
    if vendor is not None and vendor_value is not None:
        return vendor_value
    return policy_value


def _low_confidence_fields(invoice: Invoice, threshold: float) -> tuple[str, ...]:
    """Any extracted field — critical or not — below `threshold`. A field a human has
    already corrected reads at 1.0 (FR-054) and can never be "low"."""
    return tuple(
        name
        for name, extracted in invoice.fields.items()
        if extracted.effective_confidence < threshold
    )


def _low_confidence_lines(invoice: Invoice, threshold: float) -> tuple[int, ...]:
    return tuple(line.line_number for line in invoice.lines if line.row_confidence < threshold)


def _decide(
    invoice: Invoice,
    issues: list[ValidationIssue],
    match: MatchResult,
    vendor: Vendor | None,
    policy: RoutingPolicy,
) -> tuple[RoutingDecision, str]:
    hard_failures = [
        issue for issue in issues if issue.severity == Severity.HARD and not issue.passed
    ]
    if hard_failures:
        rule_id = hard_failures[0].rule_id
        reason = "duplicate" if rule_id == "BR-04" else f"validation:{rule_id}"
        return RoutingDecision.EXCEPTION, reason

    requires_po = vendor.requires_po if vendor is not None else True
    if match.status == MatchStatus.MISMATCH and requires_po:
        return RoutingDecision.EXCEPTION, "three_way_match:mismatch"
    if match.status == MatchStatus.PARTIAL:
        return RoutingDecision.REVIEW, "three_way_match:partial"

    if vendor is None or vendor.invoices_processed < policy.min_vendor_history:
        # AC-01.3: an unseen vendor is never eligible for STP, regardless of confidence.
        return RoutingDecision.REVIEW, "policy:new_vendor"

    if (
        vendor.max_auto_amount is not None
        and invoice.total is not None
        and invoice.total > vendor.max_auto_amount
    ):
        return RoutingDecision.REVIEW, "policy:amount_ceiling"

    auto_threshold = _resolve_threshold(vendor, vendor.auto_threshold, policy.auto_threshold)
    review_threshold = _resolve_threshold(vendor, vendor.review_threshold, policy.review_threshold)

    overall = min((invoice.confidence(f) for f in CRITICAL_FIELDS), default=0.0)
    low_fields = _low_confidence_fields(invoice, review_threshold)
    low_lines = _low_confidence_lines(invoice, policy.line_threshold)

    if overall >= auto_threshold and not low_fields and not low_lines:
        return RoutingDecision.STRAIGHT_THROUGH, "confidence:auto"
    if overall >= review_threshold:
        return RoutingDecision.REVIEW, "confidence:field_review"
    return RoutingDecision.FULL_REVIEW, "confidence:full_review"


def route(
    invoice: Invoice,
    issues: list[ValidationIssue],
    match: MatchResult,
    vendor: Vendor | None,
    policy: RoutingPolicy,
) -> RoutingResult:
    """Compute the routing decision for `invoice`.

    The decision is always computed, whether or not shadow mode is on — that is what lets
    a customer see "what we would have decided" before automation is switched on (README
    §3, FR-056). In shadow mode the actual decision returned is always REVIEW; the computed
    decision is carried on `shadow_decision`/`shadow_reason` for the caller to persist and
    report against (FR-058, AC-13.2).
    """
    overall = min((invoice.confidence(f) for f in CRITICAL_FIELDS), default=0.0)
    decision, reason = _decide(invoice, issues, match, vendor, policy)

    if policy.shadow_mode:
        return RoutingResult(
            decision=RoutingDecision.REVIEW,
            reason="shadow_mode",
            overall_confidence=overall,
            shadow_decision=decision,
            shadow_reason=reason,
        )
    return RoutingResult(decision=decision, reason=reason, overall_confidence=overall)
