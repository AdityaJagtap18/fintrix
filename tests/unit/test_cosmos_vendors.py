import pytest

from infrastructure.repositories.vendors import CosmosVendorRepository
from tests.unit.factories import make_vendor
from tests.unit.fakes import FakeContainer, FakeDatabase


def _make_repo() -> CosmosVendorRepository:
    database = FakeDatabase(
        containers={
            "vendors": FakeContainer(partition_key_path="/vendor_id"),
            "vendor_tax_ids": FakeContainer(partition_key_path="/tax_id"),
        }
    )
    return CosmosVendorRepository(database)


def test_save_then_get_round_trips_every_field():
    repo = _make_repo()
    vendor = make_vendor(auto_threshold=0.9, review_threshold=0.6, max_auto_amount=None)
    repo.save(vendor)

    fetched = repo.get(vendor.vendor_id)

    assert fetched == vendor


def test_get_missing_vendor_returns_none():
    assert _make_repo().get("v-does-not-exist") is None


def test_find_by_tax_id_resolves_through_the_lookup_container():
    repo = _make_repo()
    vendor = make_vendor(vendor_id="v-1", tax_id="27AAPFU0939F1ZV")
    repo.save(vendor)

    assert repo.find_by_tax_id("27AAPFU0939F1ZV") == vendor


def test_find_by_tax_id_unknown_returns_none():
    assert _make_repo().find_by_tax_id("unknown-gstin") is None


def test_active_vendors_excludes_inactive():
    repo = _make_repo()
    active = make_vendor(vendor_id="v-active", tax_id="TAX-A", is_active=True)
    inactive = make_vendor(vendor_id="v-inactive", tax_id="TAX-B", is_active=False)
    repo.save(active)
    repo.save(inactive)

    pool = repo.active_vendors()

    assert [v.vendor_id for v in pool] == ["v-active"]


def test_save_rejects_a_tax_id_already_claimed_by_another_vendor():
    repo = _make_repo()
    repo.save(make_vendor(vendor_id="v-1", tax_id="27AAPFU0939F1ZV"))

    with pytest.raises(ValueError, match="already claimed"):
        repo.save(make_vendor(vendor_id="v-2", tax_id="27AAPFU0939F1ZV"))


def test_resaving_the_same_vendor_with_the_same_tax_id_is_not_a_conflict():
    repo = _make_repo()
    vendor = make_vendor(vendor_id="v-1", tax_id="27AAPFU0939F1ZV", invoices_processed=1)
    repo.save(vendor)

    vendor.invoices_processed = 2
    repo.save(vendor)  # must not raise

    assert repo.get("v-1").invoices_processed == 2
