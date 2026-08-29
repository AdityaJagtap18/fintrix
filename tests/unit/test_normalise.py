from datetime import date
from decimal import Decimal

import pytest

from core.validation.normalise import (
    NormalisationError,
    clean_ocr_text,
    normalise_amount,
    normalise_currency,
    normalise_date,
)


@pytest.mark.parametrize(
    "raw", ["₹1,250.00", "1250", "Rs 1250.00", "Rs. 1,250", "INR 1250.00", 1250, 1250.0]
)
def test_normalise_amount_variants_agree(raw):
    # T-021 done-when: "₹1,250.00", "1250", and "Rs 1250.00" all normalise identically.
    assert normalise_amount(raw) == Decimal("1250.00")


def test_normalise_amount_handles_parenthesised_negative():
    assert normalise_amount("(500.00)") == Decimal("-500.00")


def test_normalise_amount_rounds_to_two_places():
    assert normalise_amount("10.005") == Decimal("10.01")


def test_normalise_amount_rejects_garbage():
    with pytest.raises(NormalisationError):
        normalise_amount("not a number")


def test_normalise_amount_rejects_none():
    with pytest.raises(NormalisationError):
        normalise_amount(None)


@pytest.mark.parametrize(
    "raw",
    ["2026-08-01", "01/08/2026", "1 Aug 2026", "August 1, 2026", "01-Aug-2026"],
)
def test_normalise_date_variants_agree(raw):
    assert normalise_date(raw) == date(2026, 8, 1)


def test_normalise_date_passes_through_date_object():
    assert normalise_date(date(2026, 1, 1)) == date(2026, 1, 1)


def test_normalise_date_rejects_garbage():
    with pytest.raises(NormalisationError):
        normalise_date("not a date at all !!")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("INR", "INR"),
        ("inr", "INR"),
        ("₹", "INR"),
        ("Rs", "INR"),
        ("$", "USD"),
        ("usd", "USD"),
        ("EUR", "EUR"),
    ],
)
def test_normalise_currency_variants(raw, expected):
    assert normalise_currency(raw) == expected


def test_normalise_currency_rejects_unknown_symbol():
    with pytest.raises(NormalisationError):
        normalise_currency("???")


def test_clean_ocr_text_collapses_whitespace():
    assert clean_ocr_text("Acme   Supplies\n\tPvt   Ltd") == "Acme Supplies Pvt Ltd"


def test_clean_ocr_text_handles_none():
    assert clean_ocr_text(None) == ""
