from datetime import date

from core.extraction.cache import DEFAULT_CACHE_WINDOW_DAYS, decide_cache_reuse
from tests.unit.factories import make_invoice


def test_no_prior_extraction_means_no_reuse():
    decision = decide_cache_reuse(None, cached_at=None, today=date(2026, 8, 29))
    assert decision.reuse is False
    assert decision.reason == "no_prior_extraction"


def test_fresh_cache_hit_is_reused():
    invoice = make_invoice()
    decision = decide_cache_reuse(
        invoice, cached_at=date(2026, 8, 1), today=date(2026, 8, 29), cache_window_days=90
    )
    assert decision.reuse is True
    assert decision.reason == "content_hash_match"
    assert decision.cached_invoice is invoice


def test_expired_cache_is_not_reused():
    # NFR-041 / FR-003: only within the retention window.
    invoice = make_invoice()
    decision = decide_cache_reuse(
        invoice, cached_at=date(2026, 1, 1), today=date(2026, 8, 29), cache_window_days=90
    )
    assert decision.reuse is False
    assert decision.reason == "cache_expired"


def test_exactly_at_window_boundary_is_still_reused():
    invoice = make_invoice()
    cached_at = date(2026, 6, 1)
    today = date(2026, 6, 1)  # 0 days old
    decision = decide_cache_reuse(
        invoice, cached_at=cached_at, today=today, cache_window_days=DEFAULT_CACHE_WINDOW_DAYS
    )
    assert decision.reuse is True


def test_missing_cached_at_is_not_trusted():
    invoice = make_invoice()
    decision = decide_cache_reuse(invoice, cached_at=None, today=date(2026, 8, 29))
    assert decision.reuse is False
    assert decision.reason == "cached_at_unknown"


def test_cached_at_in_future_is_rejected():
    invoice = make_invoice()
    decision = decide_cache_reuse(invoice, cached_at=date(2027, 1, 1), today=date(2026, 8, 29))
    assert decision.reuse is False
    assert decision.reason == "cached_at_in_future"
