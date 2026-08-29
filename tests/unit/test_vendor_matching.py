from dataclasses import dataclass, field

from core.matching.vendor import resolve_vendor
from core.models import Vendor


@dataclass
class FakeVendorLookup:
    vendors: list[Vendor] = field(default_factory=list)

    def find_by_tax_id(self, tax_id):
        return next((v for v in self.vendors if v.tax_id == tax_id), None)

    def active_vendors(self):
        return self.vendors


def make_vendor(**overrides):
    defaults = dict(
        vendor_id="v-1",
        name="Acme Supplies Pvt Ltd",
        normalised_name="acme supplies pvt ltd",
        tax_id="27AAPFU0939F1ZV",
    )
    defaults.update(overrides)
    return Vendor(**defaults)


def test_exact_tax_id_match_wins_over_everything():
    vendor = make_vendor()
    lookup = FakeVendorLookup([vendor])
    resolution = resolve_vendor("Completely Different Name Inc", vendor.tax_id, lookup)
    assert resolution.vendor is vendor
    assert resolution.method == "exact_tax_id"
    assert resolution.score == 100.0


def test_fuzzy_name_match_used_when_no_tax_id():
    vendor = make_vendor()
    lookup = FakeVendorLookup([vendor])
    # OCR-mangled variant of the vendor's name
    resolution = resolve_vendor("Acme Suppliess Pvt Ltd", None, lookup)
    assert resolution.vendor is vendor
    assert resolution.method == "fuzzy_name"
    assert resolution.score >= 88.0


def test_fuzzy_match_below_threshold_returns_no_vendor():
    vendor = make_vendor()
    lookup = FakeVendorLookup([vendor])
    resolution = resolve_vendor("Totally Unrelated Company", None, lookup)
    assert resolution.vendor is None
    assert resolution.method == "none"


def test_empty_vendor_pool_and_no_tax_id_returns_none():
    lookup = FakeVendorLookup([])
    resolution = resolve_vendor("Anyone", None, lookup)
    assert resolution.vendor is None
    assert resolution.method == "none"
    assert resolution.score == 0.0


def test_no_name_and_no_tax_id_match_returns_none_without_scanning():
    lookup = FakeVendorLookup([make_vendor()])
    resolution = resolve_vendor(None, "does-not-exist", lookup)
    assert resolution.vendor is None
    assert resolution.method == "none"
