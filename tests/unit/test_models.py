from datetime import date
from decimal import Decimal

import pytest

from core.models import (
    TERMINAL_STATES,
    BoundingRegion,
    ExtractedField,
    FieldSource,
    Invoice,
    InvoiceState,
)


def test_bounding_region_rejects_zero_page():
    with pytest.raises(ValueError):
        BoundingRegion(page=0, polygon=(0.0, 0.0, 1.0, 1.0))


def test_bounding_region_rejects_odd_polygon_length():
    with pytest.raises(ValueError):
        BoundingRegion(page=1, polygon=(0.0, 0.0, 1.0))


def test_extracted_field_rejects_out_of_range_confidence():
    with pytest.raises(ValueError):
        ExtractedField(name="total", value=Decimal("10"), confidence=1.5)


def test_model_confidence_used_for_model_source():
    field = ExtractedField(name="total", value=Decimal("10"), confidence=0.4)
    assert field.effective_confidence == 0.4


def test_human_source_is_always_fully_confident():
    """FR-054: a field corrected by a human is treated as fully confident, regardless of
    whatever confidence value it was stored with."""
    field = ExtractedField(
        name="total", value=Decimal("10"), confidence=0.1, source=FieldSource.HUMAN
    )
    assert field.effective_confidence == 1.0


def test_corrected_returns_new_field_with_human_source():
    original = ExtractedField(name="total", value=Decimal("10"), confidence=0.4)
    corrected = original.corrected(Decimal("12"))
    assert corrected.value == Decimal("12")
    assert corrected.source == FieldSource.HUMAN
    assert corrected.effective_confidence == 1.0
    assert corrected.needs_review is False
    # original is untouched
    assert original.value == Decimal("10")
    assert original.source == FieldSource.MODEL


def test_invoice_confidence_missing_field_reads_zero():
    invoice = Invoice(invoice_id="i1", document_id="d1")
    assert invoice.confidence("total") == 0.0
    assert invoice.value("total") is None


def test_invoice_apply_correction_creates_field_if_absent():
    invoice = Invoice(invoice_id="i1", document_id="d1")
    invoice.apply_correction("total", Decimal("500.00"))
    assert invoice.value("total") == Decimal("500.00")
    assert invoice.confidence("total") == 1.0


def test_invoice_apply_correction_overwrites_existing_field():
    invoice = Invoice(
        invoice_id="i1",
        document_id="d1",
        fields={"total": ExtractedField(name="total", value=Decimal("10"), confidence=0.3)},
    )
    invoice.apply_correction("total", Decimal("999.99"))
    assert invoice.value("total") == Decimal("999.99")
    assert invoice.confidence("total") == 1.0


def test_invoice_convenience_accessors():
    invoice = Invoice(
        invoice_id="i1",
        document_id="d1",
        fields={
            "vendor_name": ExtractedField(name="vendor_name", value="Acme", confidence=0.9),
            "invoice_date": ExtractedField(
                name="invoice_date", value=date(2026, 1, 1), confidence=0.9
            ),
        },
    )
    assert invoice.vendor_name == "Acme"
    assert invoice.invoice_date == date(2026, 1, 1)
    assert invoice.total is None


def test_terminal_states():
    assert InvoiceState.RELEASED in TERMINAL_STATES
    assert InvoiceState.REJECTED in TERMINAL_STATES
    assert InvoiceState.QUARANTINED in TERMINAL_STATES
    assert InvoiceState.IN_REVIEW not in TERMINAL_STATES
