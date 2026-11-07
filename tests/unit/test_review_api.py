"""HTTP-level tests for the review endpoints. Uses FastAPI's TestClient — no live server,
no Azure. The in-memory repository is swapped in per-test via dependency_overrides for
isolation, the same pattern real integration tests will use once T-047's SQL repository
replaces it.

Every endpoint requires a session (this router's own dependencies=[Depends(get_session)])
on top of the X-Reviewer header — any signed-in user can review, AP being the reviewer
role in practice. `_ap_headers` bundles both into one dict so every test call looks the
same as it did before this requirement landed.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from api.auth import SessionStore, hash_password
from api.dependencies import InMemoryReviewAssignmentRepository, get_review_repo
from api.main import app

_AP_PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    # One repo instance shared across every request in a test — a fresh lambda call per
    # request (the naive version of this override) would silently reset state between
    # the claim and submit calls a single test makes.
    repo = InMemoryReviewAssignmentRepository()
    app.dependency_overrides[get_review_repo] = lambda: repo
    import api.auth as auth_module

    original_sessions = auth_module._sessions
    auth_module._sessions = SessionStore()

    credentials_file = tmp_path / "credentials.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(("username", "role", "password_hash"))
    sheet.append(("ap", "ap", hash_password(_AP_PASSWORD)))
    workbook.save(credentials_file)
    monkeypatch.setenv("CREDENTIALS_XLSX_PATH", str(credentials_file))

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
    auth_module._sessions = original_sessions


def _ap_headers(client: TestClient, reviewer: str) -> dict[str, str]:
    login = client.post("/api/v1/auth/login", json={"username": "ap", "password": _AP_PASSWORD})
    token = login.json()["token"]
    return {"Authorization": f"Bearer {token}", "X-Reviewer": reviewer}


def test_health_endpoints(client: TestClient) -> None:
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 200


def test_claim_succeeds_and_returns_holder_and_expiry(client: TestClient) -> None:
    resp = client.post("/api/v1/review/inv-1/claim", headers=_ap_headers(client, "priya"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["invoice_id"] == "inv-1"
    assert body["claimed_by"] == "priya"
    assert "expires_at" in body


def test_claim_without_reviewer_header_is_unauthorized(client: TestClient) -> None:
    headers = _ap_headers(client, "priya")
    del headers["X-Reviewer"]
    resp = client.post("/api/v1/review/inv-1/claim", headers=headers)
    assert resp.status_code == 401


def test_claim_without_an_ap_session_is_unauthorized(client: TestClient) -> None:
    resp = client.post("/api/v1/review/inv-1/claim", headers={"X-Reviewer": "priya"})
    assert resp.status_code == 401


def test_ac_03_1_second_claim_returns_conflict_naming_current_holder(client: TestClient) -> None:
    client.post("/api/v1/review/inv-1/claim", headers=_ap_headers(client, "priya"))
    resp = client.post("/api/v1/review/inv-1/claim", headers=_ap_headers(client, "rajesh"))
    assert resp.status_code == 409
    assert resp.json()["detail"]["held_by"] == "priya"


def test_release_then_reclaim_by_a_different_reviewer(client: TestClient) -> None:
    client.post("/api/v1/review/inv-1/claim", headers=_ap_headers(client, "priya"))
    release_resp = client.post("/api/v1/review/inv-1/release", headers=_ap_headers(client, "priya"))
    assert release_resp.status_code == 204

    reclaim_resp = client.post("/api/v1/review/inv-1/claim", headers=_ap_headers(client, "rajesh"))
    assert reclaim_resp.status_code == 200
    assert reclaim_resp.json()["claimed_by"] == "rajesh"


def test_release_by_non_holder_is_forbidden(client: TestClient) -> None:
    client.post("/api/v1/review/inv-1/claim", headers=_ap_headers(client, "priya"))
    resp = client.post("/api/v1/review/inv-1/release", headers=_ap_headers(client, "rajesh"))
    assert resp.status_code == 403


def test_submit_by_holder_succeeds_with_rejected_outcome(client: TestClient) -> None:
    client.post("/api/v1/review/inv-1/claim", headers=_ap_headers(client, "priya"))
    resp = client.post(
        "/api/v1/review/inv-1/submit",
        headers=_ap_headers(client, "priya"),
        json={"outcome": "rejected"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["outcome"] == "rejected"
    assert body["claimed_by"] == "priya"


def test_submit_by_holder_succeeds_with_forwarded_outcome(client: TestClient) -> None:
    # AP's other real outcome: "I think this should be accepted" — final acceptance is
    # an admin's call, not AP's (api/routers/admin.py).
    client.post("/api/v1/review/inv-1/claim", headers=_ap_headers(client, "priya"))
    resp = client.post(
        "/api/v1/review/inv-1/submit",
        headers=_ap_headers(client, "priya"),
        json={"outcome": "forwarded"},
    )
    assert resp.status_code == 200
    assert resp.json()["outcome"] == "forwarded"


def test_submit_with_approved_outcome_is_forbidden_for_ap(client: TestClient) -> None:
    client.post("/api/v1/review/inv-1/claim", headers=_ap_headers(client, "priya"))
    resp = client.post(
        "/api/v1/review/inv-1/submit",
        headers=_ap_headers(client, "priya"),
        json={"outcome": "approved"},
    )
    assert resp.status_code == 403


def test_ac_03_3_submit_without_a_claim_is_forbidden(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/review/inv-1/submit",
        headers=_ap_headers(client, "priya"),
        json={"outcome": "rejected"},
    )
    assert resp.status_code == 403


def test_submit_by_wrong_reviewer_is_forbidden(client: TestClient) -> None:
    client.post("/api/v1/review/inv-1/claim", headers=_ap_headers(client, "priya"))
    resp = client.post(
        "/api/v1/review/inv-1/submit",
        headers=_ap_headers(client, "rajesh"),
        json={"outcome": "rejected"},
    )
    assert resp.status_code == 403


def test_submit_with_invalid_outcome_is_unprocessable(client: TestClient) -> None:
    client.post("/api/v1/review/inv-1/claim", headers=_ap_headers(client, "priya"))
    resp = client.post(
        "/api/v1/review/inv-1/submit",
        headers=_ap_headers(client, "priya"),
        json={"outcome": "maybe"},
    )
    assert resp.status_code == 422
