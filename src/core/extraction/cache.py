"""FR-003 / NFR-041 / constitution Article XII: identical content is never billed for
extraction twice within the retention window. This module makes the reuse decision; the
actual hash lookup is a repository concern (`InvoiceRepository.get_by_content_hash`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from core.models import Invoice

DEFAULT_CACHE_WINDOW_DAYS = 90


@dataclass(frozen=True, slots=True)
class CacheDecision:
    reuse: bool
    reason: str
    cached_invoice: Invoice | None = None


def decide_cache_reuse(
    cached_invoice: Invoice | None,
    *,
    cached_at: date | None,
    today: date,
    cache_window_days: int = DEFAULT_CACHE_WINDOW_DAYS,
) -> CacheDecision:
    """Whether a previously extracted invoice for the same content hash can be reused in
    place of billing extraction again."""
    if cached_invoice is None:
        return CacheDecision(reuse=False, reason="no_prior_extraction")
    if cached_at is None:
        # A cached result with no extraction timestamp can't be aged against the window.
        # Treat that as untrustworthy rather than assuming it's still fresh.
        return CacheDecision(reuse=False, reason="cached_at_unknown")
    if cached_at > today:
        return CacheDecision(reuse=False, reason="cached_at_in_future")
    age_days = (today - cached_at).days
    if age_days > cache_window_days:
        return CacheDecision(reuse=False, reason="cache_expired")
    return CacheDecision(reuse=True, reason="content_hash_match", cached_invoice=cached_invoice)
