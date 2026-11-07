"""HTTP-level tests for the admin endpoints: the forwarded queue, the accept action, and
the Excel export. Same fake-repo pattern as test_preprocessing_api.py — admin.py reaches
into processing.py's real store and repos, so it needs the same hermetic fakes.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import openpyxl
import pytest
from fastapi.testclient import TestClient

from api.auth import SessionStore, hash_password
from api.dependencies import (
    InMemoryReviewAssignmentRepository,
    get_audit_log_writer,
    get_document_intelligence_extractor,
    get_duplicate_lookup,
    get_invoice_admin,
    get_invoice_repo,
    get_review_repo,
    get_vendor_repo,
)
from api.main import app
from tests.unit.test_processing_api import FakeAuditLog, FakeInvoiceRepo, FakeVendorRepo

_AP_PASSWORD = "correct-horse-battery-staple"
_ADMIN_PASSWORD = "another-battery-staple"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    import api.auth as auth_module
    import api.routers.processing as processing_module

    original_store = processing_module._store
    processing_module._store = processing_module._ProcessingStore()
    original_sessions = auth_module._sessions
    auth_module._sessions = SessionStore()

    credentials_file = tmp_path / "credentials.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(("username", "role", "password_hash"))
    sheet.append(("ap", "ap", hash_password(_AP_PASSWORD)))
    sheet.append(("admin", "admin", hash_password(_ADMIN_PASSWORD)))
    workbook.save(credentials_file)
    monkeypatch.setenv("CREDENTIALS_XLSX_PATH", str(credentials_file))

    invoice_repo = FakeInvoiceRepo()
    overrides: dict[Any, Any] = {
        get_vendor_repo: FakeVendorRepo(),
        get_duplicate_lookup: invoice_repo,
        get_invoice_repo: invoice_repo,
        get_invoice_admin: invoice_repo,
        get_audit_log_writer: FakeAuditLog(),
        get_review_repo: InMemoryReviewAssignmentRepository(),
    }

    def _make_override(fake: object) -> Any:
        def _override() -> object:
            return fake

        return _override

    for dependency, fake in overrides.items():
        app.dependency_overrides[dependency] = _make_override(fake)

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.pop(get_document_intelligence_extractor, None)
    for dependency in overrides:
        app.dependency_overrides.pop(dependency, None)
    processing_module._store = original_store
    auth_module._sessions = original_sessions


def _ap_token(client: TestClient) -> str:
    resp = client.post("/api/v1/auth/login", json={"username": "ap", "password": _AP_PASSWORD})
    token: str = resp.json()["token"]
    return token


def _admin_token(client: TestClient) -> str:
    resp = client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": _ADMIN_PASSWORD}
    )
    token: str = resp.json()["token"]
    return token


def _forward_invoice(client: TestClient, invoice_id: str, reviewer: str = "priya") -> None:
    """Seeds demo invoices (via GET /results) if needed, then has AP forward `invoice_id`."""
    ap_headers = {"Authorization": f"Bearer {_ap_token(client)}", "X-Reviewer": reviewer}
    client.get("/api/v1/processing/results", headers=ap_headers)
    claim = client.post(f"/api/v1/review/{invoice_id}/claim", headers=ap_headers)
    assert claim.status_code == 200, claim.text
    submit = client.post(
        f"/api/v1/review/{invoice_id}/submit", headers=ap_headers, json={"outcome": "forwarded"}
    )
    assert submit.status_code == 200, submit.text


def test_forwarded_requires_an_admin_session(client: TestClient) -> None:
    resp = client.get("/api/v1/admin/forwarded")
    assert resp.status_code == 401


def test_forwarded_rejects_a_valid_ap_session_with_403_not_401(client: TestClient) -> None:
    # A real, currently-valid session — just not an admin one. 403 (forbidden), not 401
    # (unauthenticated): the token is genuine, it just doesn't carry the admin role.
    resp = client.get(
        "/api/v1/admin/forwarded", headers={"Authorization": f"Bearer {_ap_token(client)}"}
    )
    assert resp.status_code == 403


def test_forwarded_lists_only_forwarded_invoices(client: TestClient) -> None:
    _forward_invoice(client, "inv-demo-flagged-1")

    resp = client.get(
        "/api/v1/admin/forwarded", headers={"Authorization": f"Bearer {_admin_token(client)}"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert [r["invoice_id"] for r in body] == ["inv-demo-flagged-1"]
    assert body[0]["review_outcome"] == "forwarded"


def test_accept_requires_an_admin_session(client: TestClient) -> None:
    _forward_invoice(client, "inv-demo-flagged-1")
    resp = client.post("/api/v1/admin/inv-demo-flagged-1/accept")
    assert resp.status_code == 401


def test_accept_rejects_an_invoice_that_was_never_forwarded(client: TestClient) -> None:
    admin_headers = {"Authorization": f"Bearer {_admin_token(client)}"}
    client.get("/api/v1/processing/results", headers=admin_headers)  # seed, forward nothing
    resp = client.post("/api/v1/admin/inv-demo-clean-1/accept", headers=admin_headers)
    assert resp.status_code == 409


def test_accept_builds_a_real_payment_instruction_and_marks_it_approved(
    client: TestClient,
) -> None:
    _forward_invoice(client, "inv-demo-flagged-1")

    resp = client.post(
        "/api/v1/admin/inv-demo-flagged-1/accept",
        headers={"Authorization": f"Bearer {_admin_token(client)}"},
    )
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["review_outcome"] == "approved"
    instruction = result["payment_instruction"]
    assert instruction is not None
    # FR-085: the vendor master's bank account, same as a straight-through instruction —
    # human-approved doesn't relax where this comes from.
    assert instruction["vendor_bank_account"] == "HDFC0001234-000111222333"
    assert instruction["approval_evidence"].startswith("admin:exception:")

    # And it now shows up as accepted via the normal /results listing.
    results = client.get(
        "/api/v1/processing/results", headers={"Authorization": f"Bearer {_admin_token(client)}"}
    ).json()
    accepted = next(r for r in results if r["invoice_id"] == "inv-demo-flagged-1")
    assert accepted["payment_instruction"] is not None


def test_accept_is_no_longer_in_the_forwarded_queue_afterwards(client: TestClient) -> None:
    _forward_invoice(client, "inv-demo-flagged-1")
    admin_headers = {"Authorization": f"Bearer {_admin_token(client)}"}
    client.post("/api/v1/admin/inv-demo-flagged-1/accept", headers=admin_headers)

    resp = client.get("/api/v1/admin/forwarded", headers=admin_headers)
    assert resp.json() == []


def test_export_requires_an_admin_session(client: TestClient) -> None:
    resp = client.get("/api/v1/admin/export")
    assert resp.status_code == 401


def test_export_produces_a_real_workbook_with_only_accepted_invoices(client: TestClient) -> None:
    admin_headers = {"Authorization": f"Bearer {_admin_token(client)}"}
    client.get("/api/v1/processing/results", headers=admin_headers)  # seeds inv-demo-clean-1
    # (straight_through) and inv-demo-flagged-1 (exception, not accepted — should be excluded)

    resp = client.get("/api/v1/admin/export", headers=admin_headers)

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    workbook = openpyxl.load_workbook(io.BytesIO(resp.content))
    sheet = workbook.active
    assert sheet is not None
    rows = list(sheet.iter_rows(values_only=True))
    assert rows[0] == (
        "Vendor Name",
        "IFSC Code",
        "Account Number",
        "Amount",
        "Currency",
        "Invoice Number",
        "Due Date",
        "Reference",
    )
    assert len(rows) == 2  # header + the one accepted (straight-through) invoice
    data_row = rows[1]
    assert data_row[0] == "Acme Supplies Pvt Ltd"
    assert data_row[1] == "HDFC0001234"  # IFSC, split from the vendor's bank_account
    assert data_row[2] == "000111222333"  # account number
    assert data_row[5] == "INV-1001"


def test_export_includes_an_admin_accepted_invoice_too(client: TestClient) -> None:
    _forward_invoice(client, "inv-demo-flagged-1")
    admin_headers = {"Authorization": f"Bearer {_admin_token(client)}"}
    client.post("/api/v1/admin/inv-demo-flagged-1/accept", headers=admin_headers)

    resp = client.get("/api/v1/admin/export", headers=admin_headers)

    workbook = openpyxl.load_workbook(io.BytesIO(resp.content))
    sheet = workbook.active
    assert sheet is not None
    rows = list(sheet.iter_rows(values_only=True))
    assert len(rows) == 3  # header + both seeded demo invoices, now both accepted
    invoice_numbers = {row[5] for row in rows[1:]}
    assert invoice_numbers == {"INV-1001", "INV-1042"}
