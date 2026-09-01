from decimal import Decimal

from core.models import PurchaseOrder, PurchaseOrderLine
from infrastructure.repositories.purchase_orders import CosmosPurchaseOrderRepository
from tests.unit.fakes import FakeContainer, FakeDatabase


def _make_repo() -> CosmosPurchaseOrderRepository:
    database = FakeDatabase(
        containers={"purchase_orders": FakeContainer(partition_key_path="/vendor_id")}
    )
    return CosmosPurchaseOrderRepository(database)


def _make_po(
    po_number: str = "PO-1001",
    vendor_id: str = "v-1",
    *,
    is_closed: bool = False,
    unit_price: Decimal = Decimal("100.00"),
    quantity_ordered: Decimal = Decimal("10"),
) -> PurchaseOrder:
    return PurchaseOrder(
        po_number=po_number,
        vendor_id=vendor_id,
        is_closed=is_closed,
        lines=[
            PurchaseOrderLine(
                po_line_id=f"{po_number}-L1",
                po_number=po_number,
                line_number=1,
                product_code=None,
                unit_price=unit_price,
                quantity_ordered=quantity_ordered,
                quantity_received=quantity_ordered,
                quantity_invoiced=Decimal("0"),
            )
        ],
    )


def test_save_then_find_by_number_round_trips_lines():
    repo = _make_repo()
    po = _make_po()
    repo.save(po)

    assert repo.find_by_number("PO-1001") == po


def test_find_by_number_unknown_returns_none():
    assert _make_repo().find_by_number("PO-does-not-exist") is None


def test_find_by_vendor_and_amount_matches_within_tolerance():
    repo = _make_repo()
    # total = 100.00 * 10 = 1000.00
    repo.save(_make_po("PO-1001"))

    match = repo.find_by_vendor_and_amount("v-1", Decimal("1010.00"), Decimal("2"))

    assert match is not None
    assert match.po_number == "PO-1001"


def test_find_by_vendor_and_amount_outside_tolerance_returns_none():
    repo = _make_repo()
    repo.save(_make_po("PO-1001"))  # total = 1000.00

    match = repo.find_by_vendor_and_amount("v-1", Decimal("1500.00"), Decimal("2"))

    assert match is None


def test_find_by_vendor_and_amount_excludes_closed_pos():
    repo = _make_repo()
    repo.save(_make_po("PO-1001", is_closed=True))

    match = repo.find_by_vendor_and_amount("v-1", Decimal("1000.00"), Decimal("2"))

    assert match is None
