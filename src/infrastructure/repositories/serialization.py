"""Shared to-item/from-item helpers for the Cosmos repositories. Money is `Decimal` in
`core` (Article I) and a *string* in the Cosmos JSON item — never `float`, which would
silently reintroduce the rounding risk the domain model exists to prevent.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any


def now_utc() -> datetime:
    """Naive, but always UTC — the same convention as `now_utc()` in api/dependencies.py,
    duplicated here rather than imported: `infrastructure` must not depend on `api`
    (plan.md §2's dependency rule runs the other way)."""
    return datetime.now(UTC).replace(tzinfo=None)


def decimal_to_item(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def decimal_from_item(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)


def date_to_item(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def date_from_item(value: str | None) -> date | None:
    return None if value is None else date.fromisoformat(value)


def datetime_to_item(value: datetime | None) -> str | None:
    # Fixed microsecond precision (isoformat() otherwise omits it when zero) so range
    # queries comparing these strings lexicographically (find_expired's `<=`) never have to
    # reason about variable-width timestamps.
    return None if value is None else value.isoformat(timespec="microseconds")


def datetime_from_item(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


def encode_scalar(value: Any) -> dict[str, Any]:
    """`ExtractedField.value`/`ValidationIssue.suggested_value` are typed `Any` — this
    tags the encoding so `decode_scalar` can invert it without guessing from shape alone.
    `datetime` is checked before `date`: it's a subclass, so the reverse order would encode
    every datetime as a bare date and silently drop the time component.
    """
    if isinstance(value, Decimal):
        return {"type": "decimal", "value": str(value)}
    if isinstance(value, datetime):
        return {"type": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"type": "date", "value": value.isoformat()}
    return {"type": "raw", "value": value}


def decode_scalar(encoded: dict[str, Any]) -> Any:
    kind, value = encoded["type"], encoded["value"]
    if kind == "decimal":
        return Decimal(value)
    if kind == "datetime":
        return datetime.fromisoformat(value)
    if kind == "date":
        return date.fromisoformat(value)
    return value
