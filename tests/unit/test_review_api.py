"""HTTP-level tests for the review endpoints. Uses FastAPI's TestClient — no live server,
no Azure. The in-memory repository is swapped in per-test via dependency_overrides for
isolation, the same pattern real integration tests will use once T-047's SQL repository
replaces it.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from api.dependencies import InMemoryReviewAssignmentRepository, get_review_repo
from api.main import app


@pytest.fixture
def client() -> Iterator[TestClient]:
    # One repo instance shared across every request in a test — a fresh lambda call per
    # request (the naive version of this override) would silently reset state between
    # the claim and submit calls a single test makes.
    repo = InMemoryReviewAssignmentRepository()
    app.dependency_overrides[get_review_repo] = lambda: repo
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_health_endpoints(client: TestClient) -> None:
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 200


def test_claim_succeeds_and_returns_holder_and_expiry(client: TestClient) -> None:
    resp = client.post("/api/v1/review/inv-1/claim", headers={"X-Reviewer": "priya"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["invoice_id"] == "inv-1"
    assert body["claimed_by"] == "priya"
    assert "expires_at" in body


def test_claim_without_reviewer_header_is_unauthorized(client: TestClient) -> None:
    resp = client.post("/api/v1/review/inv-1/claim")
    assert resp.status_code == 401


def test_ac_03_1_second_claim_returns_conflict_naming_current_holder(client: TestClient) -> None:
    client.post("/api/v1/review/inv-1/claim", headers={"X-Reviewer": "priya"})
    resp = client.post("/api/v1/review/inv-1/claim", headers={"X-Reviewer": "rajesh"})
    assert resp.status_code == 409
    assert resp.json()["detail"]["held_by"] == "priya"


def test_release_then_reclaim_by_a_different_reviewer(client: TestClient) -> None:
    client.post("/api/v1/review/inv-1/claim", headers={"X-Reviewer": "priya"})
    release_resp = client.post("/api/v1/review/inv-1/release", headers={"X-Reviewer": "priya"})
    assert release_resp.status_code == 204

    reclaim_resp = client.post("/api/v1/review/inv-1/claim", headers={"X-Reviewer": "rajesh"})
    assert reclaim_resp.status_code == 200
    assert reclaim_resp.json()["claimed_by"] == "rajesh"


def test_release_by_non_holder_is_forbidden(client: TestClient) -> None:
    client.post("/api/v1/review/inv-1/claim", headers={"X-Reviewer": "priya"})
    resp = client.post("/api/v1/review/inv-1/release", headers={"X-Reviewer": "rajesh"})
    assert resp.status_code == 403


def test_submit_by_holder_succeeds_with_outcome(client: TestClient) -> None:
    client.post("/api/v1/review/inv-1/claim", headers={"X-Reviewer": "priya"})
    resp = client.post(
        "/api/v1/review/inv-1/submit", headers={"X-Reviewer": "priya"}, json={"outcome": "approved"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["outcome"] == "approved"
    assert body["claimed_by"] == "priya"


def test_ac_03_3_submit_without_a_claim_is_forbidden(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/review/inv-1/submit", headers={"X-Reviewer": "priya"}, json={"outcome": "approved"}
    )
    assert resp.status_code == 403


def test_submit_by_wrong_reviewer_is_forbidden(client: TestClient) -> None:
    client.post("/api/v1/review/inv-1/claim", headers={"X-Reviewer": "priya"})
    resp = client.post(
        "/api/v1/review/inv-1/submit",
        headers={"X-Reviewer": "rajesh"},
        json={"outcome": "rejected"},
    )
    assert resp.status_code == 403


def test_submit_with_invalid_outcome_is_unprocessable(client: TestClient) -> None:
    client.post("/api/v1/review/inv-1/claim", headers={"X-Reviewer": "priya"})
    resp = client.post(
        "/api/v1/review/inv-1/submit", headers={"X-Reviewer": "priya"}, json={"outcome": "maybe"}
    )
    assert resp.status_code == 422
