"""Cosmos DB implementation of `core.persistence.PurchaseOrderLookup`, backed by the
`purchase_orders` container (partitioned by `vendor_id`, id = `po_number`, lines embedded
inline). This is what turns `core.matching.three_way.match_invoice()`'s already-assembled
`PurchaseOrder` object into a single read instead of a SQL join.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from azure.cosmos.database import DatabaseProxy

from core.models import PurchaseOrder, PurchaseOrderLine


def _line_to_item(line: PurchaseOrderLine) -> dict[str, Any]:
    return {
        "po_line_id": line.po_line_id,
        "po_number": line.po_number,
        "line_number": line.line_number,
        "product_code": line.product_code,
        "unit_price": str(line.unit_price),
        "quantity_ordered": str(line.quantity_ordered),
        "quantity_received": str(line.quantity_received),
        "quantity_invoiced": str(line.quantity_invoiced),
    }


def _line_from_item(item: dict[str, Any]) -> PurchaseOrderLine:
    return PurchaseOrderLine(
        po_line_id=item["po_line_id"],
        po_number=item["po_number"],
        line_number=item["line_number"],
        product_code=item.get("product_code"),
        unit_price=Decimal(item["unit_price"]),
        quantity_ordered=Decimal(item["quantity_ordered"]),
        quantity_received=Decimal(item["quantity_received"]),
        quantity_invoiced=Decimal(item["quantity_invoiced"]),
    )


def _to_item(po: PurchaseOrder) -> dict[str, Any]:
    return {
        "id": po.po_number,
        "po_number": po.po_number,
        "vendor_id": po.vendor_id,
        "is_closed": po.is_closed,
        "lines": [_line_to_item(line) for line in po.lines],
    }


def _from_item(item: dict[str, Any]) -> PurchaseOrder:
    return PurchaseOrder(
        po_number=item["po_number"],
        vendor_id=item["vendor_id"],
        is_closed=item["is_closed"],
        lines=[_line_from_item(line) for line in item["lines"]],
    )


class CosmosPurchaseOrderRepository:
    """Satisfies `PurchaseOrderLookup` structurally (Article I). `save` isn't part of that
    Protocol — POs are mirrored in from the customer's ERP export (plan.md §3), not written
    by `core` — but is provided here as the natural counterpart for whatever ingests that
    export (T-048+).
    """

    def __init__(self, database: DatabaseProxy) -> None:
        self._purchase_orders = database.get_container_client("purchase_orders")

    def save(self, po: PurchaseOrder) -> None:
        self._purchase_orders.upsert_item(_to_item(po))

    def find_by_number(self, po_number: str) -> PurchaseOrder | None:
        # No vendor context at this call site (FR-040: resolution starts from a bare PO
        # number extracted off the invoice) — necessarily cross-partition. See plan.md's
        # tradeoffs; Cosmos's id-filter fan-out isn't a full collection scan.
        items = self._purchase_orders.query_items(
            query="SELECT * FROM c WHERE c.id = @po_number",
            parameters=[{"name": "@po_number", "value": po_number}],
            enable_cross_partition_query=True,
        )
        for item in items:
            return _from_item(item)
        return None

    def find_by_vendor_and_amount(
        self, vendor_id: str, amount: Decimal, tolerance_pct: Decimal
    ) -> PurchaseOrder | None:
        """FR-040's fallback when no PO number was extracted. Fetches the vendor's open POs
        (single-partition — cheap, a vendor has few open POs at once) and compares each
        one's line total in Python, the same "fetch the pool, filter in Python" pattern
        already accepted for `CosmosVendorRepository.active_vendors()`."""
        items = self._purchase_orders.query_items(
            query="SELECT * FROM c WHERE c.vendor_id = @vendor_id AND c.is_closed = false",
            parameters=[{"name": "@vendor_id", "value": vendor_id}],
            partition_key=vendor_id,
        )
        for item in items:
            po = _from_item(item)
            total = sum(
                (line.unit_price * line.quantity_ordered for line in po.lines), Decimal("0")
            )
            if total == 0:
                continue
            # tolerance_pct is a 0-100 percentage, matching
            # core.matching.three_way._pct_difference's convention.
            diff_pct = abs(total - amount) / total * 100
            if diff_pct <= tolerance_pct:
                return po
        return None
