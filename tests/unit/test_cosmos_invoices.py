from datetime import date
from decimal import Decimal

import pytest
from azure.cosmos.exceptions import CosmosResourceExistsError

from infrastructure.repositories.invoices import CosmosInvoiceRepository
from infrastructure.repositories.vendors import CosmosVendorRepository
from tests.unit.factories import VALID_GSTIN, make_invoice, make_line, make_vendor
from tests.unit.fakes import FakeContainer, FakeDatabase


def _make_database() -> FakeDatabase:
    return FakeDatabase(
        containers={
            "invoices": FakeContainer(
                partition_key_path="/vendor_id", unique_key_paths=("/invoice_number",)
            ),
            "documents": FakeContainer(partition_key_path="/content_hash"),
            "vendor_tax_ids": FakeContainer(partition_key_path="/tax_id"),
            "vendors": FakeContainer(partition_key_path="/vendor_id"),
        }
    )


def _make_repo(database: FakeDatabase | None = None) -> CosmosInvoiceRepository:
    return CosmosInvoiceRepository(database or _make_database())


def _seed_tax_id(database: FakeDatabase, tax_id: str, vendor_id: str) -> None:
    # Goes through the real VendorRepository.save() rather than hand-writing the lookup
    # item, so this test doesn't assume anything about vendor_tax_ids' internal shape
    # beyond what CosmosVendorRepository already commits to.
    CosmosVendorRepository(database).save(make_vendor(vendor_id=vendor_id, tax_id=tax_id))


def test_save_then_get_round_trips_fields_and_lines():
    repo = _make_repo()
    invoice = make_invoice(lines=[make_line(1), make_line(2, description="Widget B")])
    invoice.vendor_id = "v-1"
    repo.save(invoice)

    fetched = repo.get(invoice.invoice_id)

    assert fetched == invoice


def test_get_missing_invoice_returns_none():
    assert _make_repo().get("inv-does-not-exist") is None


def test_unresolved_vendor_id_round_trips_as_none():
    repo = _make_repo()
    invoice = make_invoice()  # vendor_id left at its default, None
    repo.save(invoice)

    assert repo.get(invoice.invoice_id).vendor_id is None


def test_get_by_content_hash_resolves_through_documents():
    repo = _make_repo()
    # documents' item id is the content_hash itself (see invoices.py's comment) —
    # document_id is just a regular property here.
    repo._documents.create_item(
        {
            "id": "hash-abc",
            "content_hash": "hash-abc",
            "document_id": "doc-1",
            "blob_uri": "raw/doc-1.pdf",
        }
    )
    invoice = make_invoice(invoice_id="inv-1", document_id="doc-1")
    invoice.vendor_id = "v-1"
    repo.save(invoice)

    assert repo.get_by_content_hash("hash-abc") == invoice
    assert repo.get_by_content_hash("hash-unknown") is None


def test_exact_duplicate_exists_resolves_tax_id_to_vendor_then_checks_invoice_number():
    database = _make_database()
    _seed_tax_id(database, VALID_GSTIN, "v-1")
    repo = _make_repo(database)
    invoice = make_invoice(invoice_id="inv-1", invoice_number="INV-1001")
    invoice.vendor_id = "v-1"
    repo.save(invoice)

    assert repo.exact_duplicate_exists(VALID_GSTIN, "INV-1001") is True
    assert repo.exact_duplicate_exists(VALID_GSTIN, "INV-9999") is False


def test_exact_duplicate_exists_excludes_the_invoice_itself():
    database = _make_database()
    _seed_tax_id(database, VALID_GSTIN, "v-1")
    repo = _make_repo(database)
    invoice = make_invoice(invoice_id="inv-1", invoice_number="INV-1001")
    invoice.vendor_id = "v-1"
    repo.save(invoice)

    assert repo.exact_duplicate_exists(VALID_GSTIN, "INV-1001", exclude_invoice_id="inv-1") is False


def test_exact_duplicate_exists_returns_false_for_an_unknown_tax_id():
    assert _make_repo().exact_duplicate_exists("unknown-gstin", "INV-1001") is False


def test_find_near_duplicates_filters_by_vendor_amount_and_date_window():
    repo = _make_repo()
    in_window = make_invoice(
        invoice_id="inv-in-window",
        invoice_number="INV-A",
        invoice_date=date(2026, 8, 3),
        total=Decimal("1180.00"),
    )
    in_window.vendor_id = "v-1"
    outside_window = make_invoice(
        invoice_id="inv-outside-window",
        invoice_number="INV-B",
        invoice_date=date(2026, 8, 20),
        total=Decimal("1180.00"),
    )
    outside_window.vendor_id = "v-1"
    different_amount = make_invoice(
        invoice_id="inv-different-amount",
        invoice_number="INV-C",
        invoice_date=date(2026, 8, 2),
        total=Decimal("50.00"),
    )
    different_amount.vendor_id = "v-1"
    for inv in (in_window, outside_window, different_amount):
        repo.save(inv)

    matches = repo.find_near_duplicates("v-1", Decimal("1180.00"), date(2026, 8, 1), 3)

    assert matches == ["inv-in-window"]


def test_saving_a_second_invoice_with_a_duplicate_invoice_number_in_the_same_partition_raises():
    repo = _make_repo()
    first = make_invoice(invoice_id="inv-1", invoice_number="INV-1001")
    first.vendor_id = "v-1"
    second = make_invoice(invoice_id="inv-2", invoice_number="INV-1001")
    second.vendor_id = "v-1"
    repo.save(first)

    with pytest.raises(CosmosResourceExistsError):
        repo.save(second)
