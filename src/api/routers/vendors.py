"""Vendor directory endpoint for the FINTRIX UI."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from api.inmemory_repositories import InMemoryVendorStore, get_vendor_store

router = APIRouter(prefix="/api/v1/vendors", tags=["vendors"])

Store = Annotated[InMemoryVendorStore, Depends(get_vendor_store)]


@router.get("")
def list_vendors(store: Store) -> list[dict[str, Any]]:
    return [
        {
            "vendorId": v.vendor_id,
            "name": v.name,
            "taxId": v.tax_id,
            "invoicesProcessed": v.invoices_processed,
            "isActive": v.is_active,
        }
        for v in sorted(store.list(), key=lambda v: v.invoices_processed, reverse=True)
    ]
