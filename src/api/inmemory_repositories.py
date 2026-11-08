"""In-memory stores backing the invoices/vendors/dashboard endpoints (dev/demo placeholder
— see module docstring in `seed_data.py` and `dependencies.py`'s existing
`InMemoryReviewAssignmentRepository` for the established pattern). T-047 (SQL/Azure SQL
repositories) replaces these; routers depend on the method signatures here, not on the
fact that storage is a Python list.
"""

from __future__ import annotations

import builtins

from api.seed_data import SEED_INVOICES, SEED_VENDORS, InvoiceRecord
from core.models import Vendor


class InMemoryInvoiceStore:
    def __init__(self, records: list[InvoiceRecord]) -> None:
        self._records: dict[str, InvoiceRecord] = {r.invoice.invoice_id: r for r in records}

    def list(self) -> list[InvoiceRecord]:
        return list(self._records.values())

    def get(self, invoice_id: str) -> InvoiceRecord | None:
        return self._records.get(invoice_id)

    def add(self, record: InvoiceRecord) -> None:
        self._records[record.invoice.invoice_id] = record

    def set_status(self, invoice_id: str, status: str) -> None:
        if invoice_id in self._records:
            self._records[invoice_id].status = status

    def apply_correction(self, invoice_id: str, field_name: str, value: object) -> None:
        record = self._records.get(invoice_id)
        if record is not None:
            record.invoice.apply_correction(field_name, value)


class InMemoryVendorStore:
    def __init__(self, vendors: list[Vendor]) -> None:
        self._vendors: dict[str, Vendor] = {v.vendor_id: v for v in vendors}

    def list(self) -> list[Vendor]:
        return list(self._vendors.values())

    def get(self, vendor_id: str) -> Vendor | None:
        return self._vendors.get(vendor_id)

    # core.persistence.VendorLookup — lets this double as the vendor pool for
    # core.matching.vendor.resolve_vendor (BR-11/12) against the same seeded vendors the
    # UI already shows, rather than a separate throwaway lookup just for uploads.
    def find_by_tax_id(self, tax_id: str) -> Vendor | None:
        return next((v for v in self._vendors.values() if v.tax_id == tax_id), None)

    def active_vendors(self) -> builtins.list[Vendor]:
        return [v for v in self._vendors.values() if v.is_active]


_invoice_store = InMemoryInvoiceStore(list(SEED_INVOICES))
_vendor_store = InMemoryVendorStore(list(SEED_VENDORS))


def get_invoice_store() -> InMemoryInvoiceStore:
    return _invoice_store


def get_vendor_store() -> InMemoryVendorStore:
    return _vendor_store
