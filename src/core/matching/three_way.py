"""Three-way match: invoice ↔ purchase order ↔ goods receipt (FR-040..044, AC-06.1..06.5).

Goods-receipt quantities are folded into `PurchaseOrderLine.quantity_received_unbilled`
upstream (received minus already-invoiced) so this module only ever compares against one
number per line, not two.
"""

from __future__ import annotations

from decimal import Decimal

from core.models import (
    Invoice,
    LineItem,
    MatchResult,
    MatchStatus,
    MatchVariance,
    PurchaseOrder,
    PurchaseOrderLine,
)

DEFAULT_PRICE_TOLERANCE_PCT = Decimal("2")


def _pct_difference(expected: Decimal, actual: Decimal) -> Decimal:
    if expected == 0:
        return Decimal("100") if actual != 0 else Decimal("0")
    return ((actual - expected) / expected * 100).copy_abs()


def _match_po_line(line: LineItem, po: PurchaseOrder) -> PurchaseOrderLine | None:
    """Match an invoice line to a PO line by product code first (unambiguous), falling
    back to position when neither side carries a code — common on simple single-line
    service invoices."""
    if line.product_code:
        for po_line in po.lines:
            if po_line.product_code and po_line.product_code == line.product_code:
                return po_line
    index = line.line_number - 1
    if 0 <= index < len(po.lines):
        return po.lines[index]
    return None


def match_invoice(
    invoice: Invoice,
    po: PurchaseOrder | None,
    *,
    vendor_requires_po: bool,
    price_tolerance_pct: Decimal = DEFAULT_PRICE_TOLERANCE_PCT,
) -> MatchResult:
    """Reconcile `invoice` against `po`.

    AC-06.5: a vendor configured as not requiring a PO with no PO reference on the invoice
    matches NOT_APPLICABLE and routing continues on confidence alone — this is not a
    failure state.
    """
    if invoice.po_number is None and not vendor_requires_po:
        return MatchResult(
            status=MatchStatus.NOT_APPLICABLE,
            message="vendor does not require PO matching and none was referenced",
        )

    if po is None:
        return MatchResult(
            status=MatchStatus.MISMATCH,
            message=f"no purchase order could be resolved for reference {invoice.po_number!r}",
            variances=(
                MatchVariance(
                    line_number=None,
                    kind="not_found",
                    expected=None,
                    actual=None,
                    difference=None,
                    difference_pct=None,
                    message="purchase order not found",
                ),
            ),
        )

    if po.is_closed:
        return MatchResult(
            status=MatchStatus.MISMATCH,
            message=f"purchase order {po.po_number} is closed",
            variances=(
                MatchVariance(
                    line_number=None,
                    kind="closed_po",
                    expected=None,
                    actual=None,
                    difference=None,
                    difference_pct=None,
                    message=f"PO {po.po_number} is closed and cannot accept further invoices",
                ),
            ),
        )

    variances: list[MatchVariance] = []
    for line in invoice.lines:
        po_line = _match_po_line(line, po)
        if po_line is None:
            variances.append(
                MatchVariance(
                    line_number=line.line_number,
                    kind="not_found",
                    expected=None,
                    actual=None,
                    difference=None,
                    difference_pct=None,
                    message=f"line {line.line_number} has no corresponding PO line",
                )
            )
            continue

        price_diff = (line.unit_price - po_line.unit_price).copy_abs()
        price_diff_pct = _pct_difference(po_line.unit_price, line.unit_price)
        if price_diff_pct > price_tolerance_pct:
            variances.append(
                MatchVariance(
                    line_number=line.line_number,
                    kind="price",
                    expected=po_line.unit_price,
                    actual=line.unit_price,
                    difference=price_diff,
                    difference_pct=price_diff_pct,
                    message=(
                        f"line {line.line_number}: unit price {line.unit_price} vs PO "
                        f"{po_line.unit_price} ({price_diff_pct.quantize(Decimal('0.1'))}% over)"
                    ),
                )
            )

        unbilled = po_line.quantity_received_unbilled
        if line.quantity > unbilled:
            shortfall = line.quantity - unbilled
            variances.append(
                MatchVariance(
                    line_number=line.line_number,
                    kind="quantity",
                    expected=unbilled,
                    actual=line.quantity,
                    difference=shortfall,
                    difference_pct=_pct_difference(unbilled, line.quantity) if unbilled else None,
                    message=(
                        f"line {line.line_number}: invoiced {line.quantity} exceeds "
                        f"received-and-unbilled {unbilled} by {shortfall}"
                    ),
                )
            )

    if variances:
        noun = "discrepancy" if len(variances) == 1 else "discrepancies"
        return MatchResult(
            status=MatchStatus.MISMATCH,
            message=f"{len(variances)} line {noun} found",
            variances=tuple(variances),
        )

    return MatchResult(status=MatchStatus.MATCHED, message=f"matched against PO {po.po_number}")
