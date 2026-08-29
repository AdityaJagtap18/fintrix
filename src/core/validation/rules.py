"""The business rule catalogue (spec.md §8, BR-01..BR-16) as a decorator-registered set of
composable rules. Adding a rule means adding a function decorated `@rule("BR-xx")` — nothing
else changes, in particular never `routing.confidence_router` (FR-034, NFR-052).

Every rule always returns a `ValidationIssue`, passing or failing (FR-030) — the catalogue
records what it checked, not only what it caught.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from core.models import Invoice, Severity, ValidationIssue, VendorResolution
from core.persistence import DuplicateLookup

DEFAULT_APPROVED_CURRENCIES = frozenset({"INR", "USD", "EUR", "GBP", "AED", "SGD", "JPY", "AUD"})


@dataclass(slots=True)
class ValidationContext:
    """Everything a rule needs, precomputed by earlier pipeline stages (vendor resolution
    happens in stage 6, before validation in stage 7a — see plan.md §4) so no rule performs
    its own I/O."""

    invoice: Invoice
    vendor_resolution: VendorResolution
    duplicate_lookup: DuplicateLookup
    today: date
    amount_tolerance: Decimal = Decimal("1.00")
    name_match_threshold: float = 88.0
    max_invoice_age_days: int = 365
    near_duplicate_window_days: int = 3
    approved_currencies: frozenset[str] = field(default_factory=lambda: DEFAULT_APPROVED_CURRENCIES)
    max_plausible_tax_rate_pct: Decimal = Decimal("30")
    is_credit_note: bool = False


RuleFunc = Callable[[ValidationContext], ValidationIssue]
_RULES: dict[str, RuleFunc] = {}


def rule(rule_id: str) -> Callable[[RuleFunc], RuleFunc]:
    def decorator(fn: RuleFunc) -> RuleFunc:
        if rule_id in _RULES:
            raise ValueError(f"duplicate rule id {rule_id!r} — every BR-xx must be unique")
        _RULES[rule_id] = fn
        return fn

    return decorator


def validate(ctx: ValidationContext) -> list[ValidationIssue]:
    """Evaluate the full catalogue in rule-id order and return every outcome (FR-030)."""
    return [_RULES[rule_id](ctx) for rule_id in sorted(_RULES)]


def registered_rule_ids() -> tuple[str, ...]:
    return tuple(sorted(_RULES))


# --------------------------------------------------------------------------------------
# BR-01 — required fields present
# --------------------------------------------------------------------------------------
@rule("BR-01")
def required_fields_present(ctx: ValidationContext) -> ValidationIssue:
    inv = ctx.invoice
    required = {
        "vendor_name": inv.vendor_name,
        "invoice_number": inv.invoice_number,
        "invoice_date": inv.invoice_date,
        "total": inv.total,
    }
    missing = tuple(name for name, value in required.items() if value is None)
    passed = not missing
    return ValidationIssue(
        rule_id="BR-01",
        severity=Severity.HARD,
        passed=passed,
        message="all required fields present"
        if passed
        else f"missing required fields: {', '.join(missing)}",
        fields=missing,
    )


# --------------------------------------------------------------------------------------
# BR-02 — line item amounts sum to subtotal within tolerance
# --------------------------------------------------------------------------------------
@rule("BR-02")
def line_items_sum_to_subtotal(ctx: ValidationContext) -> ValidationIssue:
    inv = ctx.invoice
    if inv.subtotal is None or not inv.lines:
        return ValidationIssue(
            "BR-02",
            Severity.HARD,
            True,
            "no line items or subtotal to reconcile",
            fields=("subtotal",),
        )
    computed = sum((line.amount for line in inv.lines), Decimal("0"))
    diff = (computed - inv.subtotal).copy_abs()
    passed = diff <= ctx.amount_tolerance
    return ValidationIssue(
        "BR-02",
        Severity.HARD,
        passed,
        f"line items sum to {computed}, matches subtotal {inv.subtotal}"
        if passed
        else f"line items sum to {computed}, subtotal states {inv.subtotal} (diff {diff})",
        fields=("subtotal",),
        suggested_value=None if passed else computed,
    )


# --------------------------------------------------------------------------------------
# BR-03 — subtotal plus tax equals total within tolerance
# --------------------------------------------------------------------------------------
@rule("BR-03")
def subtotal_plus_tax_equals_total(ctx: ValidationContext) -> ValidationIssue:
    inv = ctx.invoice
    if inv.subtotal is None or inv.tax_total is None or inv.total is None:
        return ValidationIssue(
            "BR-03", Severity.HARD, True, "insufficient data to reconcile total", fields=("total",)
        )
    computed = inv.subtotal + inv.tax_total
    diff = (computed - inv.total).copy_abs()
    passed = diff <= ctx.amount_tolerance
    return ValidationIssue(
        "BR-03",
        Severity.HARD,
        passed,
        f"subtotal + tax = {computed}, matches total {inv.total}"
        if passed
        else f"subtotal + tax = {computed}, total states {inv.total} (diff {diff} over tolerance)",
        fields=("total",),
        suggested_value=None if passed else computed,
    )


# --------------------------------------------------------------------------------------
# BR-04 — exact duplicate on (vendor_tax_id, invoice_number)
# --------------------------------------------------------------------------------------
@rule("BR-04")
def exact_duplicate(ctx: ValidationContext) -> ValidationIssue:
    inv = ctx.invoice
    tax_id, number = inv.vendor_tax_id, inv.invoice_number
    if not tax_id or not number:
        return ValidationIssue(
            "BR-04",
            Severity.HARD,
            True,
            "insufficient data to check for duplicates",
            fields=("vendor_tax_id", "invoice_number"),
        )
    is_duplicate = ctx.duplicate_lookup.exact_duplicate_exists(
        tax_id, number, exclude_invoice_id=inv.invoice_id
    )
    passed = not is_duplicate
    return ValidationIssue(
        "BR-04",
        Severity.HARD,
        passed,
        "no exact duplicate found"
        if passed
        else f"invoice {number} for vendor tax id {tax_id} has already been processed",
        fields=("vendor_tax_id", "invoice_number"),
    )


# --------------------------------------------------------------------------------------
# BR-05 — near-duplicate on (vendor, amount, date ±3d)
# --------------------------------------------------------------------------------------
@rule("BR-05")
def near_duplicate(ctx: ValidationContext) -> ValidationIssue:
    inv = ctx.invoice
    vendor = ctx.vendor_resolution.vendor
    if vendor is None or inv.total is None or inv.invoice_date is None:
        return ValidationIssue(
            "BR-05", Severity.SOFT, True, "insufficient data to check near-duplicates"
        )
    matches = ctx.duplicate_lookup.find_near_duplicates(
        vendor.vendor_id, inv.total, inv.invoice_date, ctx.near_duplicate_window_days
    )
    passed = not matches
    return ValidationIssue(
        "BR-05",
        Severity.SOFT,
        passed,
        "no near-duplicates found"
        if passed
        else f"possible near-duplicate of previously processed invoice(s): {', '.join(matches)}",
        fields=("total", "invoice_date"),
    )


# --------------------------------------------------------------------------------------
# BR-06 — invoice date is not in the future
# --------------------------------------------------------------------------------------
@rule("BR-06")
def invoice_date_not_future(ctx: ValidationContext) -> ValidationIssue:
    inv = ctx.invoice
    if inv.invoice_date is None:
        return ValidationIssue(
            "BR-06", Severity.HARD, True, "no invoice date to check", fields=("invoice_date",)
        )
    passed = inv.invoice_date <= ctx.today
    return ValidationIssue(
        "BR-06",
        Severity.HARD,
        passed,
        "invoice date is not in the future"
        if passed
        else f"invoice date {inv.invoice_date} is after today ({ctx.today})",
        fields=("invoice_date",),
    )


# --------------------------------------------------------------------------------------
# BR-07 — invoice date is within the accepted age window
# --------------------------------------------------------------------------------------
@rule("BR-07")
def invoice_within_age_window(ctx: ValidationContext) -> ValidationIssue:
    inv = ctx.invoice
    if inv.invoice_date is None:
        return ValidationIssue(
            "BR-07", Severity.SOFT, True, "no invoice date to check", fields=("invoice_date",)
        )
    age_days = (ctx.today - inv.invoice_date).days
    passed = age_days <= ctx.max_invoice_age_days
    return ValidationIssue(
        "BR-07",
        Severity.SOFT,
        passed,
        f"invoice is {age_days} days old"
        if passed
        else f"invoice is {age_days} days old, exceeds the {ctx.max_invoice_age_days}-day window",
        fields=("invoice_date",),
    )


# --------------------------------------------------------------------------------------
# BR-08 — due date is on or after invoice date
# --------------------------------------------------------------------------------------
@rule("BR-08")
def due_date_on_or_after_invoice_date(ctx: ValidationContext) -> ValidationIssue:
    inv = ctx.invoice
    if inv.invoice_date is None or inv.due_date is None:
        return ValidationIssue(
            "BR-08",
            Severity.HARD,
            True,
            "insufficient data to check due date",
            fields=("invoice_date", "due_date"),
        )
    passed = inv.due_date >= inv.invoice_date
    return ValidationIssue(
        "BR-08",
        Severity.HARD,
        passed,
        "due date is on or after invoice date"
        if passed
        else f"due date {inv.due_date} precedes invoice date {inv.invoice_date}",
        fields=("due_date",),
        suggested_value=None if passed else inv.invoice_date,
    )


# --------------------------------------------------------------------------------------
# BR-09 — tax identifier format and checksum
# --------------------------------------------------------------------------------------
_GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
_GSTIN_CODE_POINTS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _gstin_checksum_valid(gstin: str) -> bool:
    """Standard mod-36 GSTIN check-digit algorithm (15th character)."""
    total = 0
    for i, ch in enumerate(gstin[:-1]):
        factor = 1 if i % 2 == 0 else 2
        product = _GSTIN_CODE_POINTS.index(ch) * factor
        total += product // 36 + product % 36
    expected = _GSTIN_CODE_POINTS[(36 - (total % 36)) % 36]
    return expected == gstin[-1]


@rule("BR-09")
def tax_id_format_and_checksum(ctx: ValidationContext) -> ValidationIssue:
    inv = ctx.invoice
    raw = inv.vendor_tax_id
    if not raw:
        return ValidationIssue(
            "BR-09", Severity.HARD, True, "no tax id to validate", fields=("vendor_tax_id",)
        )
    candidate = raw.strip().upper()
    if _GSTIN_RE.match(candidate):
        passed = _gstin_checksum_valid(candidate)
        return ValidationIssue(
            "BR-09",
            Severity.HARD,
            passed,
            "GSTIN checksum valid" if passed else f"GSTIN {candidate} fails checksum validation",
            fields=("vendor_tax_id",),
        )
    # Non-GSTIN jurisdictions are blocked on spec.md Q-04 (which jurisdictions' tax IDs
    # must validate at launch is still open). Until answered, a plausible-looking non-empty
    # ID passes rather than silently blocking STP for every non-Indian vendor.
    passed = len(candidate) >= 4
    return ValidationIssue(
        "BR-09",
        Severity.HARD,
        passed,
        "tax id present; jurisdiction-specific format validation pending spec.md Q-04"
        if passed
        else f"tax id {candidate!r} is too short to be plausible",
        fields=("vendor_tax_id",),
    )


# --------------------------------------------------------------------------------------
# BR-10 — currency is in the approved list
# --------------------------------------------------------------------------------------
@rule("BR-10")
def currency_approved(ctx: ValidationContext) -> ValidationIssue:
    inv = ctx.invoice
    if not inv.currency:
        return ValidationIssue(
            "BR-10", Severity.HARD, False, "no currency extracted", fields=("currency",)
        )
    passed = inv.currency in ctx.approved_currencies
    return ValidationIssue(
        "BR-10",
        Severity.HARD,
        passed,
        f"currency {inv.currency} is approved"
        if passed
        else f"currency {inv.currency} is not in the approved list",
        fields=("currency",),
    )


# --------------------------------------------------------------------------------------
# BR-11 — vendor resolves to the vendor master
# --------------------------------------------------------------------------------------
@rule("BR-11")
def vendor_resolves(ctx: ValidationContext) -> ValidationIssue:
    res = ctx.vendor_resolution
    passed = res.vendor is not None
    return ValidationIssue(
        "BR-11",
        Severity.HARD,
        passed,
        f"vendor resolved via {res.method}"
        if passed
        else "vendor could not be resolved to the vendor master",
        fields=("vendor_name", "vendor_tax_id"),
    )


# --------------------------------------------------------------------------------------
# BR-12 — vendor name fuzzy match score at least the configured threshold
# --------------------------------------------------------------------------------------
@rule("BR-12")
def vendor_name_fuzzy_score(ctx: ValidationContext) -> ValidationIssue:
    res = ctx.vendor_resolution
    if res.method == "exact_tax_id":
        return ValidationIssue(
            "BR-12",
            Severity.SOFT,
            True,
            "vendor matched by exact tax id; name score not required",
            fields=("vendor_name",),
        )
    passed = res.score >= ctx.name_match_threshold
    return ValidationIssue(
        "BR-12",
        Severity.SOFT,
        passed,
        f"vendor name match score {res.score:.1f}"
        if passed
        else f"vendor name match {res.score:.1f} is below threshold {ctx.name_match_threshold}",
        fields=("vendor_name",),
        suggested_value=res.vendor.name if (res.vendor and not passed) else None,
    )


# --------------------------------------------------------------------------------------
# BR-13 — payee bank details match the vendor master (fraud control)
# --------------------------------------------------------------------------------------
def _normalise_account(value: str) -> str:
    return re.sub(r"[\s-]", "", value).upper()


@rule("BR-13")
def bank_details_match_master(ctx: ValidationContext) -> ValidationIssue:
    inv = ctx.invoice
    vendor = ctx.vendor_resolution.vendor
    remittance = inv.value("remittance_bank_account")
    if vendor is None or not vendor.bank_account or not remittance:
        return ValidationIssue(
            "BR-13",
            Severity.HARD,
            True,
            "insufficient data to check bank details",
            fields=("remittance_bank_account",),
        )
    passed = _normalise_account(str(remittance)) == _normalise_account(vendor.bank_account)
    return ValidationIssue(
        "BR-13",
        Severity.HARD,
        passed,
        "payee bank details match the vendor master"
        if passed
        else "payee bank account differs from vendor master — possible payment redirection fraud",
        fields=("remittance_bank_account",),
    )


# --------------------------------------------------------------------------------------
# BR-14 — tax rate is plausible
# --------------------------------------------------------------------------------------
@rule("BR-14")
def tax_rate_plausible(ctx: ValidationContext) -> ValidationIssue:
    inv = ctx.invoice
    if inv.subtotal is None or inv.tax_total is None or inv.subtotal == 0:
        return ValidationIssue(
            "BR-14",
            Severity.SOFT,
            True,
            "insufficient data to assess tax rate",
            fields=("tax_total",),
        )
    rate = inv.tax_total / inv.subtotal * 100
    passed = Decimal("0") <= rate <= ctx.max_plausible_tax_rate_pct
    return ValidationIssue(
        "BR-14",
        Severity.SOFT,
        passed,
        f"effective tax rate {rate:.1f}%"
        if passed
        else f"effective tax rate {rate:.1f}% is implausible",
        fields=("tax_total",),
    )


# --------------------------------------------------------------------------------------
# BR-15 — total does not exceed the vendor's configured maximum invoice value
# --------------------------------------------------------------------------------------
@rule("BR-15")
def total_within_vendor_ceiling(ctx: ValidationContext) -> ValidationIssue:
    inv = ctx.invoice
    vendor = ctx.vendor_resolution.vendor
    if vendor is None or vendor.max_auto_amount is None or inv.total is None:
        return ValidationIssue(
            "BR-15", Severity.SOFT, True, "no vendor ceiling configured", fields=("total",)
        )
    passed = inv.total <= vendor.max_auto_amount
    return ValidationIssue(
        "BR-15",
        Severity.SOFT,
        passed,
        f"total {inv.total} is within the vendor ceiling {vendor.max_auto_amount}"
        if passed
        else f"total {inv.total} exceeds the vendor ceiling {vendor.max_auto_amount}",
        fields=("total",),
    )


# --------------------------------------------------------------------------------------
# BR-16 — line item quantities and unit prices are non-negative (credit notes excepted)
# --------------------------------------------------------------------------------------
@rule("BR-16")
def line_values_non_negative(ctx: ValidationContext) -> ValidationIssue:
    if ctx.is_credit_note:
        return ValidationIssue(
            "BR-16", Severity.HARD, True, "credit note — negative values are expected"
        )
    negative_lines = [
        line.line_number for line in ctx.invoice.lines if line.quantity < 0 or line.unit_price < 0
    ]
    passed = not negative_lines
    return ValidationIssue(
        "BR-16",
        Severity.HARD,
        passed,
        "all line quantities and prices are non-negative"
        if passed
        else f"negative quantity or price on line(s): {', '.join(map(str, negative_lines))}",
        fields=("lines",),
    )
