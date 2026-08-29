"""Normalisation: dates to ISO 8601, amounts to two-place Decimal, currency to ISO 4217
(FR-025). Runs once, in-process, between extraction and validation — everything downstream
assumes normalised values and never re-parses raw OCR text.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from dateutil import parser as date_parser

# Currency symbols we recognise without additional context. A symbol alone is ambiguous
# ($ is used by a dozen currencies) — this is a best-effort default for the common cases
# a vendor's own `currency` extraction should already override.
_CURRENCY_SYMBOLS = {
    "₹": "INR",
    "rs": "INR",
    "rs.": "INR",
    "inr": "INR",
    "$": "USD",
    "usd": "USD",
    "€": "EUR",
    "eur": "EUR",
    "£": "GBP",
    "gbp": "GBP",
    "¥": "JPY",
    "jpy": "JPY",
}

_ISO_4217_RE = re.compile(r"^[A-Z]{3}$")

# Matches a leading currency symbol/code and any thousands separators or whitespace so
# "₹1,250.00", "Rs 1250.00", and "1250" all reduce to the same Decimal.
_AMOUNT_STRIP_RE = re.compile(r"[₹$€£,\s]|(?i:rs\.?|inr|usd|eur|gbp|jpy)")


class NormalisationError(ValueError):
    """Raised when a raw value cannot be confidently normalised. Callers should treat this
    as a low-confidence / needs-review signal rather than letting it propagate as a crash —
    BR-01 (required fields present) will catch the resulting gap."""


def normalise_amount(raw: str | int | float | Decimal | None) -> Decimal:
    """Parse a currency amount to a two-decimal-place Decimal.

    Accepts already-numeric input, and strings with currency symbols/codes, thousands
    separators, and surrounding whitespace: "₹1,250.00", "Rs 1250.00", "1250" all yield
    Decimal("1250.00").
    """
    if raw is None:
        raise NormalisationError("amount is None")
    if isinstance(raw, Decimal):
        value = raw
    elif isinstance(raw, (int, float)):
        value = Decimal(str(raw))
    else:
        text = _AMOUNT_STRIP_RE.sub("", str(raw)).strip()
        # Parenthesised amounts are a common accounting convention for negatives.
        negative = text.startswith("(") and text.endswith(")")
        text = text.strip("()")
        if not text:
            raise NormalisationError(f"could not parse amount from {raw!r}")
        try:
            value = Decimal(text)
        except InvalidOperation as exc:
            raise NormalisationError(f"could not parse amount from {raw!r}") from exc
        if negative:
            value = -value
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def normalise_date(raw: str | date | datetime | None) -> date:
    """Parse a date to an ISO 8601 `date`. Accepts already-`date`/`datetime` input and
    free-form strings in common invoice date formats (dd/mm/yyyy, Month d yyyy, yyyy-mm-dd,
    ...). Day-first is assumed for ambiguous numeric dates, matching the invoice locales
    this system targets (India, UK) — this is a deliberate default, not a guess left to
    dateutil, and should move to vendor-level configuration if a US-formatted vendor shows up.
    """
    if raw is None:
        raise NormalisationError("date is None")
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    text = str(raw).strip()
    if not text:
        raise NormalisationError("empty date string")
    # An unambiguous ISO string (YYYY-MM-DD) is parsed directly — dateutil's `dayfirst`
    # flag, needed for dd/mm/yyyy invoices, otherwise misreads the month/day order here.
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass
    try:
        parsed: datetime = date_parser.parse(text, dayfirst=True, fuzzy=True)
    except (ValueError, OverflowError) as exc:
        raise NormalisationError(f"could not parse date from {raw!r}") from exc
    return parsed.date()


def normalise_currency(raw: str | None) -> str:
    """Map a currency symbol, code, or word to an ISO 4217 code."""
    if raw is None:
        raise NormalisationError("currency is None")
    text = str(raw).strip()
    if not text:
        raise NormalisationError("empty currency string")
    upper = text.upper()
    if _ISO_4217_RE.match(upper):
        return upper
    key = text.lower()
    if key in _CURRENCY_SYMBOLS:
        return _CURRENCY_SYMBOLS[key]
    raise NormalisationError(f"unrecognised currency {raw!r}")


def clean_ocr_text(raw: str | None) -> str:
    """Collapse whitespace and strip common OCR artefacts (stray pipes, double spaces)
    from free-text fields such as vendor name and address before they are used in fuzzy
    matching."""
    if raw is None:
        return ""
    text = raw.replace("|", "").replace(" ", " ")
    return re.sub(r"\s+", " ", text).strip()
