"""Cosmos DB implementation of `core.persistence.InvoiceRepository` + `DuplicateLookup`,
backed by the `invoices` container (partitioned by `vendor_id`, unique-keyed on
`invoice_number` within that partition — BR-04) plus read-only hops into `documents` and
`vendor_tax_ids`. One class, not two: both Protocols are fundamentally about `invoices`, and
splitting them would just duplicate the container-client wiring.
"""

from __future__ import annotations

import contextlib
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from azure.cosmos.database import DatabaseProxy
from azure.cosmos.exceptions import CosmosResourceNotFoundError

from core.models import (
    BoundingRegion,
    ExtractedField,
    FieldSource,
    Invoice,
    InvoiceState,
    LineItem,
)
from infrastructure.repositories.serialization import (
    date_to_item,
    datetime_from_item,
    datetime_to_item,
    decimal_to_item,
    decode_scalar,
    encode_scalar,
)

# invoices.vendor_id is nullable in core.models.Invoice, but Cosmos Hash partitioning
# doesn't want a null partition key value in practice — see plan.md §3's "invoices"
# section. Every unresolved invoice lands in this one partition until vendor resolution
# assigns it a real vendor_id.
_UNRESOLVED_VENDOR = "unresolved"


def _field_to_item(f: ExtractedField) -> dict[str, Any]:
    return {
        "name": f.name,
        "value": encode_scalar(f.value),
        "confidence": f.confidence,
        "page": f.page,
        "region": None
        if f.region is None
        else {"page": f.region.page, "polygon": list(f.region.polygon)},
        "source": f.source.value,
        "needs_review": f.needs_review,
    }


def _field_from_item(item: dict[str, Any]) -> ExtractedField:
    region = item.get("region")
    return ExtractedField(
        name=item["name"],
        value=decode_scalar(item["value"]),
        confidence=item["confidence"],
        page=item.get("page"),
        region=None
        if region is None
        else BoundingRegion(page=region["page"], polygon=tuple(region["polygon"])),
        source=FieldSource(item["source"]),
        needs_review=item["needs_review"],
    )


def _line_to_item(line: LineItem) -> dict[str, Any]:
    return {
        "line_number": line.line_number,
        "description": line.description,
        "quantity": str(line.quantity),
        "unit_price": str(line.unit_price),
        "amount": str(line.amount),
        "tax_rate": decimal_to_item(line.tax_rate),
        "product_code": line.product_code,
        "row_confidence": line.row_confidence,
        "matched_po_line_id": line.matched_po_line_id,
    }


def _line_from_item(item: dict[str, Any]) -> LineItem:
    tax_rate = item.get("tax_rate")
    return LineItem(
        line_number=item["line_number"],
        description=item["description"],
        quantity=Decimal(item["quantity"]),
        unit_price=Decimal(item["unit_price"]),
        amount=Decimal(item["amount"]),
        tax_rate=None if tax_rate is None else Decimal(tax_rate),
        product_code=item.get("product_code"),
        row_confidence=item["row_confidence"],
        matched_po_line_id=item.get("matched_po_line_id"),
    )


def _to_item(invoice: Invoice) -> dict[str, Any]:
    return {
        "id": invoice.invoice_id,
        "invoice_id": invoice.invoice_id,
        "document_id": invoice.document_id,
        "vendor_id": invoice.vendor_id or _UNRESOLVED_VENDOR,
        "state": invoice.state.value,
        "model_id": invoice.model_id,
        "model_version": invoice.model_version,
        "extracted_at": datetime_to_item(invoice.extracted_at),
        "content_hash": invoice.content_hash,
        "fields": {name: _field_to_item(f) for name, f in invoice.fields.items()},
        "lines": [_line_to_item(line) for line in invoice.lines],
        # Denormalized purely so BR-04's unique key policy and the duplicate-detection
        # queries below can filter on them — core.models.Invoice re-derives all three as
        # properties off `fields`, so they're not part of the round-trip contract.
        "invoice_number": invoice.invoice_number,
        "invoice_date": date_to_item(invoice.invoice_date),
        "total": decimal_to_item(invoice.total),
    }


def _from_item(item: dict[str, Any]) -> Invoice:
    vendor_id = item["vendor_id"]
    return Invoice(
        invoice_id=item["invoice_id"],
        document_id=item["document_id"],
        fields={name: _field_from_item(f) for name, f in item["fields"].items()},
        lines=[_line_from_item(line) for line in item["lines"]],
        vendor_id=None if vendor_id == _UNRESOLVED_VENDOR else vendor_id,
        state=InvoiceState(item["state"]),
        model_id=item.get("model_id"),
        model_version=item.get("model_version"),
        extracted_at=datetime_from_item(item.get("extracted_at")),
        content_hash=item.get("content_hash"),
    )


class CosmosInvoiceRepository:
    """Satisfies `InvoiceRepository`, `DuplicateLookup`, and `InvoiceAdmin` structurally
    (Article I)."""

    def __init__(self, database: DatabaseProxy) -> None:
        self._invoices = database.get_container_client("invoices")
        self._tax_ids = database.get_container_client("vendor_tax_ids")

    def get(self, invoice_id: str) -> Invoice | None:
        # An invoice_id alone doesn't tell us the vendor_id partition — necessarily
        # cross-partition, same tradeoff as PurchaseOrderLookup.find_by_number.
        items = self._invoices.query_items(
            query="SELECT * FROM c WHERE c.id = @invoice_id",
            parameters=[{"name": "@invoice_id", "value": invoice_id}],
            enable_cross_partition_query=True,
        )
        for item in items:
            return _from_item(item)
        return None

    def save(self, invoice: Invoice) -> None:
        self._invoices.upsert_item(_to_item(invoice))

    def delete(self, invoice_id: str) -> None:
        """InvoiceAdmin. Needs a partition-key hop first — `invoice_id` alone doesn't say
        which `vendor_id` partition the item lives in, same reasoning as `get()`."""
        items = self._invoices.query_items(
            query="SELECT c.vendor_id FROM c WHERE c.id = @invoice_id",
            parameters=[{"name": "@invoice_id", "value": invoice_id}],
            enable_cross_partition_query=True,
        )
        for item in items:
            with contextlib.suppress(CosmosResourceNotFoundError):  # already gone — idempotent
                self._invoices.delete_item(invoice_id, partition_key=item["vendor_id"])
            return

    def get_by_content_hash(self, content_hash: str) -> Invoice | None:
        """FR-003: reuse a stored extraction result instead of re-extracting.
        Cross-partition on `invoices` directly, since vendor_id (the partition key) isn't
        known at this point in the pipeline — extraction hasn't run yet, that's the whole
        question this answers. Originally hopped through the `documents` container
        (partitioned by content_hash, so that half would have been a point read) — nothing
        in this codebase writes a `documents` record yet (that's ingestion's job, T-049),
        so that path always missed. content_hash is denormalized directly onto the
        `invoices` item instead, same as invoice_number/invoice_date/total already are.
        """
        items = self._invoices.query_items(
            query="SELECT * FROM c WHERE c.content_hash = @content_hash",
            parameters=[{"name": "@content_hash", "value": content_hash}],
            enable_cross_partition_query=True,
        )
        for item in items:
            return _from_item(item)
        return None

    # --- DuplicateLookup ---

    def exact_duplicate_exists(
        self, vendor_tax_id: str, invoice_number: str, exclude_invoice_id: str | None = None
    ) -> bool:
        vendor_id = self._resolve_vendor_id(vendor_tax_id)
        if vendor_id is None:
            return False  # no vendor resolved under this tax id — nothing to duplicate
        query = (
            "SELECT * FROM c WHERE c.vendor_id = @vendor_id AND c.invoice_number = @invoice_number"
        )
        parameters: list[dict[str, object]] = [
            {"name": "@vendor_id", "value": vendor_id},
            {"name": "@invoice_number", "value": invoice_number},
        ]
        if exclude_invoice_id is not None:
            query += " AND c.id != @exclude_id"
            parameters.append({"name": "@exclude_id", "value": exclude_invoice_id})
        items = self._invoices.query_items(
            query=query, parameters=parameters, partition_key=vendor_id
        )
        return any(True for _ in items)

    def find_near_duplicates(
        self, vendor_id: str, amount: Decimal, invoice_date: date, window_days: int
    ) -> list[str]:
        window_start = invoice_date - timedelta(days=window_days)
        window_end = invoice_date + timedelta(days=window_days)
        items = self._invoices.query_items(
            query=(
                "SELECT * FROM c WHERE c.vendor_id = @vendor_id AND c.total = @amount "
                "AND c.invoice_date >= @start AND c.invoice_date <= @end"
            ),
            parameters=[
                {"name": "@vendor_id", "value": vendor_id},
                {"name": "@amount", "value": decimal_to_item(amount)},
                {"name": "@start", "value": date_to_item(window_start)},
                {"name": "@end", "value": date_to_item(window_end)},
            ],
            partition_key=vendor_id,
        )
        return [item["invoice_id"] for item in items]

    def _resolve_vendor_id(self, tax_id: str) -> str | None:
        try:
            item = self._tax_ids.read_item(tax_id, partition_key=tax_id)
        except CosmosResourceNotFoundError:
            return None
        vendor_id: str = item["vendor_id"]
        return vendor_id
