"""Cosmos DB implementation of `core.persistence.VendorRepository` (+ `VendorLookup`),
backed by the `vendors` and `vendor_tax_ids` containers declared in
`infra/modules/cosmos.bicep`.
"""

from __future__ import annotations

from typing import Any

from azure.cosmos.database import DatabaseProxy
from azure.cosmos.exceptions import CosmosResourceNotFoundError

from core.models import Vendor
from infrastructure.repositories.serialization import decimal_from_item, decimal_to_item


def _to_item(vendor: Vendor) -> dict[str, Any]:
    return {
        "id": vendor.vendor_id,
        "vendor_id": vendor.vendor_id,
        "name": vendor.name,
        "normalised_name": vendor.normalised_name,
        "tax_id": vendor.tax_id,
        "address": vendor.address,
        "bank_account": vendor.bank_account,
        "currency": vendor.currency,
        "requires_po": vendor.requires_po,
        "auto_threshold": vendor.auto_threshold,
        "review_threshold": vendor.review_threshold,
        "max_auto_amount": decimal_to_item(vendor.max_auto_amount),
        "invoices_processed": vendor.invoices_processed,
        "is_active": vendor.is_active,
    }


def _from_item(item: dict[str, Any]) -> Vendor:
    return Vendor(
        vendor_id=item["vendor_id"],
        name=item["name"],
        normalised_name=item["normalised_name"],
        tax_id=item.get("tax_id"),
        address=item.get("address"),
        bank_account=item.get("bank_account"),
        currency=item.get("currency"),
        requires_po=item["requires_po"],
        auto_threshold=item.get("auto_threshold"),
        review_threshold=item.get("review_threshold"),
        max_auto_amount=decimal_from_item(item.get("max_auto_amount")),
        invoices_processed=item["invoices_processed"],
        is_active=item["is_active"],
    )


class CosmosVendorRepository:
    """Satisfies `VendorRepository`/`VendorLookup` structurally (core depends on the
    Protocol, not this class — Article I)."""

    def __init__(self, database: DatabaseProxy) -> None:
        self._vendors = database.get_container_client("vendors")
        self._tax_ids = database.get_container_client("vendor_tax_ids")

    def get(self, vendor_id: str) -> Vendor | None:
        try:
            return _from_item(self._vendors.read_item(vendor_id, partition_key=vendor_id))
        except CosmosResourceNotFoundError:
            return None

    def find_by_tax_id(self, tax_id: str) -> Vendor | None:
        vendor_id = self._resolve_tax_id(tax_id)
        return None if vendor_id is None else self.get(vendor_id)

    def active_vendors(self) -> list[Vendor]:
        # BR-12's whole candidate pool — filtered fuzzy-matched in Python by
        # core.matching.vendor.resolve_vendor, not here (see plan.md §1's "Deliberately
        # excluded" entry: nothing upstream ever relied on a DB-side trigram search).
        items = self._vendors.query_items(
            query="SELECT * FROM c WHERE c.is_active = true",
            enable_cross_partition_query=True,
        )
        return [_from_item(item) for item in items]

    def save(self, vendor: Vendor) -> None:
        """**Known gap:** the `vendor_tax_ids` and `vendors` writes below are not atomic —
        Cosmos transactional batches only span one container's one partition, and these two
        containers use different partition keys (`/tax_id` vs `/vendor_id`). A crash
        between them leaves an orphaned lookup row. No saga/compensation in this pass.
        """
        if vendor.tax_id is not None:
            self._claim_tax_id(vendor.tax_id, vendor.vendor_id)
        self._vendors.upsert_item(_to_item(vendor))

    def _resolve_tax_id(self, tax_id: str) -> str | None:
        try:
            item = self._tax_ids.read_item(tax_id, partition_key=tax_id)
        except CosmosResourceNotFoundError:
            return None
        vendor_id: str = item["vendor_id"]
        return vendor_id

    def _claim_tax_id(self, tax_id: str, vendor_id: str) -> None:
        owner = self._resolve_tax_id(tax_id)
        if owner is None:
            self._tax_ids.create_item({"id": tax_id, "tax_id": tax_id, "vendor_id": vendor_id})
        elif owner != vendor_id:
            raise ValueError(f"tax id {tax_id!r} is already claimed by vendor {owner!r}")
        # else: this vendor already owns it — nothing to do.
