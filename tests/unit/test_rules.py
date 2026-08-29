from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from core.models import ExtractedField, Severity, Vendor, VendorResolution
from core.validation.rules import ValidationContext, registered_rule_ids, validate
from tests.unit.factories import VALID_GSTIN, make_invoice, make_line


@dataclass
class FakeDuplicateLookup:
    exact: bool = False
    near: list[str] = field(default_factory=list)

    def exact_duplicate_exists(self, vendor_tax_id, invoice_number, exclude_invoice_id=None):
        return self.exact

    def find_near_duplicates(self, vendor_id, amount, invoice_date, window_days):
        return self.near


def make_vendor(**overrides) -> Vendor:
    defaults = dict(
        vendor_id="v-1",
        name="Acme Supplies Pvt Ltd",
        normalised_name="acme supplies pvt ltd",
        tax_id=VALID_GSTIN,
        bank_account="000111222333",
        requires_po=True,
        invoices_processed=50,
    )
    defaults.update(overrides)
    return Vendor(**defaults)


def make_context(
    invoice,
    *,
    vendor=None,
    resolution=None,
    duplicate_lookup=None,
    today=date(2026, 8, 15),
    **overrides,
):
    if resolution is None:
        resolution = VendorResolution(
            vendor=vendor,
            method="exact_tax_id" if vendor else "none",
            score=100.0 if vendor else 0.0,
        )
    return ValidationContext(
        invoice=invoice,
        vendor_resolution=resolution,
        duplicate_lookup=duplicate_lookup or FakeDuplicateLookup(),
        today=today,
        **overrides,
    )


def outcome(issues, rule_id):
    return next(i for i in issues if i.rule_id == rule_id)


def test_all_sixteen_rules_registered():
    assert registered_rule_ids() == tuple(f"BR-{n:02d}" for n in range(1, 17))


def test_clean_invoice_passes_every_hard_rule():
    invoice = make_invoice(
        lines=[
            make_line(
                1, quantity=Decimal("10"), unit_price=Decimal("100.00"), amount=Decimal("1000.00")
            )
        ]
    )
    vendor = make_vendor()
    ctx = make_context(invoice, vendor=vendor)
    issues = validate(ctx)
    hard_failures = [i for i in issues if i.severity == Severity.HARD and not i.passed]
    assert hard_failures == []


# --- BR-01 ---
def test_br01_fails_when_total_missing():
    invoice = make_invoice(total=None)
    issue = outcome(validate(make_context(invoice)), "BR-01")
    assert not issue.passed
    assert "total" in issue.fields


# --- BR-02 ---
def test_br02_fails_when_lines_do_not_sum_to_subtotal():
    invoice = make_invoice(
        subtotal=Decimal("1000.00"), lines=[make_line(1, amount=Decimal("500.00"))]
    )
    issue = outcome(validate(make_context(invoice)), "BR-02")
    assert not issue.passed
    assert issue.suggested_value == Decimal("500.00")


def test_br02_passes_within_tolerance():
    invoice = make_invoice(
        subtotal=Decimal("1000.00"), lines=[make_line(1, amount=Decimal("1000.50"))]
    )
    issue = outcome(validate(make_context(invoice, amount_tolerance=Decimal("1.00"))), "BR-02")
    assert issue.passed


# --- BR-03 ---
def test_br03_fails_when_total_does_not_reconcile():
    invoice = make_invoice(
        subtotal=Decimal("1000.00"), tax_total=Decimal("180.00"), total=Decimal("2000.00")
    )
    issue = outcome(validate(make_context(invoice)), "BR-03")
    assert not issue.passed
    assert issue.suggested_value == Decimal("1180.00")


# --- BR-04 / BR-05 duplicates ---
def test_br04_fails_on_exact_duplicate():
    invoice = make_invoice()
    ctx = make_context(invoice, duplicate_lookup=FakeDuplicateLookup(exact=True))
    issue = outcome(validate(ctx), "BR-04")
    assert not issue.passed


def test_br05_soft_fails_on_near_duplicate_and_links_prior_document():
    invoice = make_invoice()
    vendor = make_vendor()
    ctx = make_context(
        invoice, vendor=vendor, duplicate_lookup=FakeDuplicateLookup(near=["inv-old-1"])
    )
    issue = outcome(validate(ctx), "BR-05")
    assert issue.severity == Severity.SOFT
    assert not issue.passed
    assert "inv-old-1" in issue.message


# --- BR-06 / BR-07 dates ---
def test_br06_fails_when_invoice_date_in_future():
    invoice = make_invoice(invoice_date=date(2099, 1, 1))
    issue = outcome(validate(make_context(invoice, today=date(2026, 8, 15))), "BR-06")
    assert not issue.passed


def test_br07_soft_fails_when_invoice_too_old():
    invoice = make_invoice(invoice_date=date(2020, 1, 1))
    issue = outcome(validate(make_context(invoice, today=date(2026, 8, 15))), "BR-07")
    assert issue.severity == Severity.SOFT
    assert not issue.passed


# --- BR-08 ---
def test_br08_fails_and_suggests_invoice_date_when_due_date_precedes_it():
    invoice = make_invoice(invoice_date=date(2026, 8, 10), due_date=date(2026, 8, 1))
    issue = outcome(validate(make_context(invoice)), "BR-08")
    assert not issue.passed
    assert issue.suggested_value == date(2026, 8, 10)


# --- BR-09 GSTIN ---
def test_br09_passes_valid_gstin():
    invoice = make_invoice(vendor_tax_id=VALID_GSTIN)
    issue = outcome(validate(make_context(invoice)), "BR-09")
    assert issue.passed


def test_br09_fails_gstin_with_transposed_digit():
    # tasks.md T-026 done-when: a GSTIN with one transposed digit fails checksum.
    mangled = VALID_GSTIN[:7] + "9309" + VALID_GSTIN[11:]  # swap "0939" -> "9309"
    assert mangled != VALID_GSTIN
    invoice = make_invoice(vendor_tax_id=mangled)
    issue = outcome(validate(make_context(invoice)), "BR-09")
    assert issue.severity == Severity.HARD
    assert not issue.passed


# --- BR-10 currency ---
def test_br10_fails_unapproved_currency():
    invoice = make_invoice(currency="XYZ")
    issue = outcome(validate(make_context(invoice)), "BR-10")
    assert not issue.passed


# --- BR-11 / BR-12 vendor resolution ---
def test_br11_fails_when_vendor_unresolved():
    invoice = make_invoice()
    ctx = make_context(invoice, resolution=VendorResolution(vendor=None, method="none", score=40.0))
    issue = outcome(validate(ctx), "BR-11")
    assert not issue.passed


def test_br12_soft_fails_below_fuzzy_threshold():
    invoice = make_invoice()
    vendor = make_vendor()
    ctx = make_context(
        invoice, resolution=VendorResolution(vendor=vendor, method="fuzzy_name", score=70.0)
    )
    issue = outcome(validate(ctx), "BR-12")
    assert issue.severity == Severity.SOFT
    assert not issue.passed
    assert issue.suggested_value == vendor.name


def test_br12_skipped_when_matched_by_exact_tax_id():
    invoice = make_invoice()
    ctx = make_context(invoice, vendor=make_vendor())
    issue = outcome(validate(ctx), "BR-12")
    assert issue.passed


# --- BR-13 bank details (fraud control) ---
def test_br13_fails_on_bank_account_mismatch_regardless_of_confidence():
    # AC-05.1: blocked regardless of extraction confidence — confidence isn't even an input
    # to this rule, by design.
    invoice = make_invoice(
        extra_fields={
            "remittance_bank_account": ExtractedField(
                name="remittance_bank_account", value="999-888-777", confidence=0.99
            )
        }
    )
    vendor = make_vendor(bank_account="000-111-222")
    ctx = make_context(invoice, vendor=vendor)
    issue = outcome(validate(ctx), "BR-13")
    assert issue.severity == Severity.HARD
    assert not issue.passed


def test_br13_passes_on_bank_account_match_ignoring_formatting():
    invoice = make_invoice(
        extra_fields={
            "remittance_bank_account": ExtractedField(
                name="remittance_bank_account", value="000 111 222", confidence=0.5
            )
        }
    )
    vendor = make_vendor(bank_account="000-111-222")
    ctx = make_context(invoice, vendor=vendor)
    issue = outcome(validate(ctx), "BR-13")
    assert issue.passed


# --- BR-14 tax plausibility ---
def test_br14_soft_fails_implausible_tax_rate():
    invoice = make_invoice(
        subtotal=Decimal("1000.00"), tax_total=Decimal("900.00"), total=Decimal("1900.00")
    )
    issue = outcome(validate(make_context(invoice)), "BR-14")
    assert issue.severity == Severity.SOFT
    assert not issue.passed


# --- BR-15 vendor ceiling ---
def test_br15_soft_fails_when_total_exceeds_vendor_ceiling():
    invoice = make_invoice(total=Decimal("50000.00"))
    vendor = make_vendor(max_auto_amount=Decimal("10000.00"))
    issue = outcome(validate(make_context(invoice, vendor=vendor)), "BR-15")
    assert issue.severity == Severity.SOFT
    assert not issue.passed


# --- BR-16 non-negative lines ---
def test_br16_fails_negative_line_values():
    invoice = make_invoice(lines=[make_line(1, quantity=Decimal("-5"))])
    issue = outcome(validate(make_context(invoice)), "BR-16")
    assert not issue.passed


def test_br16_allows_negative_values_on_credit_notes():
    invoice = make_invoice(lines=[make_line(1, quantity=Decimal("-5"))])
    ctx = make_context(invoice, is_credit_note=True)
    issue = outcome(validate(ctx), "BR-16")
    assert issue.passed
