from decimal import Decimal

from core.matching.three_way import match_invoice
from core.models import MatchStatus, PurchaseOrder, PurchaseOrderLine
from tests.unit.factories import make_invoice, make_line


def make_po_line(**overrides):
    defaults = dict(
        po_line_id="pol-1",
        po_number="PO-1",
        line_number=1,
        product_code=None,
        unit_price=Decimal("100.00"),
        quantity_ordered=Decimal("100"),
        quantity_received=Decimal("100"),
        quantity_invoiced=Decimal("0"),
    )
    defaults.update(overrides)
    return PurchaseOrderLine(**defaults)


def test_no_po_and_vendor_does_not_require_one_is_not_applicable():
    invoice = make_invoice(po_number=None)
    result = match_invoice(invoice, po=None, vendor_requires_po=False)
    assert result.status == MatchStatus.NOT_APPLICABLE


def test_po_not_found_when_vendor_requires_po_is_mismatch():
    invoice = make_invoice(po_number="PO-999")
    result = match_invoice(invoice, po=None, vendor_requires_po=True)
    assert result.status == MatchStatus.MISMATCH
    assert result.variances[0].kind == "not_found"


def test_closed_po_is_mismatch():
    invoice = make_invoice(po_number="PO-1", lines=[make_line(1)])
    po = PurchaseOrder(po_number="PO-1", vendor_id="v-1", is_closed=True, lines=[make_po_line()])
    result = match_invoice(invoice, po=po, vendor_requires_po=True)
    assert result.status == MatchStatus.MISMATCH
    assert any(v.kind == "closed_po" for v in result.variances)


def test_price_and_quantity_within_tolerance_matches():
    invoice = make_invoice(
        po_number="PO-1",
        lines=[make_line(1, quantity=Decimal("10"), unit_price=Decimal("100.00"))],
    )
    po = PurchaseOrder(
        po_number="PO-1",
        vendor_id="v-1",
        lines=[
            make_po_line(
                unit_price=Decimal("100.00"),
                quantity_received=Decimal("50"),
                quantity_invoiced=Decimal("0"),
            )
        ],
    )
    result = match_invoice(invoice, po=po, vendor_requires_po=True)
    assert result.status == MatchStatus.MATCHED


def test_unit_price_over_tolerance_is_mismatch_with_currency_and_pct():
    # AC-06.2: variance stated in currency and percentage.
    invoice = make_invoice(
        po_number="PO-1",
        lines=[make_line(1, quantity=Decimal("10"), unit_price=Decimal("110.00"))],
    )
    po = PurchaseOrder(
        po_number="PO-1",
        vendor_id="v-1",
        lines=[make_po_line(unit_price=Decimal("100.00"), quantity_received=Decimal("50"))],
    )
    result = match_invoice(
        invoice, po=po, vendor_requires_po=True, price_tolerance_pct=Decimal("2")
    )
    assert result.status == MatchStatus.MISMATCH
    variance = next(v for v in result.variances if v.kind == "price")
    assert variance.difference == Decimal("10.00")
    assert variance.difference_pct == Decimal("10")


def test_quantity_exceeding_received_unbilled_is_mismatch_with_shortfall():
    # AC-06.3: shortfall stated per line.
    invoice = make_invoice(
        po_number="PO-1",
        lines=[make_line(1, quantity=Decimal("80"), unit_price=Decimal("100.00"))],
    )
    po = PurchaseOrder(
        po_number="PO-1",
        vendor_id="v-1",
        lines=[
            make_po_line(
                unit_price=Decimal("100.00"),
                quantity_received=Decimal("50"),
                quantity_invoiced=Decimal("0"),
            )
        ],
    )
    result = match_invoice(invoice, po=po, vendor_requires_po=True)
    assert result.status == MatchStatus.MISMATCH
    variance = next(v for v in result.variances if v.kind == "quantity")
    assert variance.difference == Decimal("30")  # 80 invoiced - 50 unbilled


def test_line_with_no_matching_po_line_is_mismatch():
    invoice = make_invoice(po_number="PO-1", lines=[make_line(1), make_line(2)])
    po = PurchaseOrder(po_number="PO-1", vendor_id="v-1", lines=[make_po_line(line_number=1)])
    result = match_invoice(invoice, po=po, vendor_requires_po=True)
    assert result.status == MatchStatus.MISMATCH
    assert any(v.kind == "not_found" and v.line_number == 2 for v in result.variances)


def test_product_code_match_takes_priority_over_line_position():
    invoice = make_invoice(
        po_number="PO-1",
        lines=[
            make_line(1, product_code="SKU-2", quantity=Decimal("5"), unit_price=Decimal("50.00"))
        ],
    )
    po = PurchaseOrder(
        po_number="PO-1",
        vendor_id="v-1",
        lines=[
            make_po_line(line_number=1, product_code="SKU-1", unit_price=Decimal("999.00")),
            make_po_line(
                line_number=2,
                product_code="SKU-2",
                unit_price=Decimal("50.00"),
                quantity_received=Decimal("10"),
            ),
        ],
    )
    result = match_invoice(invoice, po=po, vendor_requires_po=True)
    assert result.status == MatchStatus.MATCHED
