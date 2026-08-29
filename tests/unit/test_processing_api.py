"""HTTP-level tests for the processing-results endpoint. Same TestClient, no-live-server
pattern as test_review_api.py.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.routers.processing import _cache, _DemoResultsCache


@pytest.fixture
def client() -> Iterator[TestClient]:
    # Swap in a fresh cache per test — the module-level singleton would otherwise leak
    # its computed-once result across tests in the same process.
    original = _cache._results
    _cache._results = None
    with TestClient(app) as test_client:
        yield test_client
    _cache._results = original


def test_results_returns_both_demo_invoices(client: TestClient) -> None:
    resp = client.get("/api/v1/processing/results")
    assert resp.status_code == 200
    body = resp.json()
    assert [r["invoice_id"] for r in body] == ["inv-demo-clean-1", "inv-demo-flagged-1"]


def test_clean_invoice_goes_straight_through_with_a_payment_instruction(
    client: TestClient,
) -> None:
    body = client.get("/api/v1/processing/results").json()
    clean = next(r for r in body if r["invoice_id"] == "inv-demo-clean-1")

    assert clean["decision"] == "straight_through"
    assert clean["issues"] == []
    instruction = clean["payment_instruction"]
    assert instruction is not None
    assert instruction["invoice_reference"] == "INV-1001"
    # FR-085: bank reference is the vendor master's, never anything from the invoice.
    assert instruction["vendor_bank_account"] == "HDFC0001234-000111222333"
    assert instruction["amount"] == "1180.00"


def test_flagged_invoice_is_an_exception_with_no_payment_instruction(client: TestClient) -> None:
    body = client.get("/api/v1/processing/results").json()
    flagged = next(r for r in body if r["invoice_id"] == "inv-demo-flagged-1")

    assert flagged["decision"] == "exception"
    assert flagged["payment_instruction"] is None
    assert any(issue["rule_id"] == "BR-03" for issue in flagged["issues"])


def test_results_are_stable_across_repeated_requests(client: TestClient) -> None:
    # Regression guard: a second GET must not re-run hand_off with a fresh timestamp and
    # trip AlreadyHandedOffError against the first request's cached instruction.
    first = client.get("/api/v1/processing/results")
    second = client.get("/api/v1/processing/results")
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()


def test_demo_results_cache_is_lazy_and_memoized() -> None:
    cache = _DemoResultsCache()
    assert cache._results is None
    first = cache.get()
    assert cache._results is first
    assert cache.get() is first
