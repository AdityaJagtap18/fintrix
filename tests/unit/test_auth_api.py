"""HTTP-level tests for the single login/logout endpoint. See api/auth.py's docstring for
why this is an Excel-backed, bcrypt-hashed credential store rather than Entra ID, and why
there's one session store (a token carries the role it was issued for) rather than two.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from api.auth import SessionStore, hash_password
from api.main import app

_PASSWORDS = {"ap": "correct-horse-battery-staple", "admin": "another-battery-staple"}
_ROLES = ("ap", "admin")
_HEADER = (
    "username",
    "role",
    "password_hash",
    "doc_quota",
    "docs_used",
    "valid_until",
    "created_at",
)


def _write_credentials(path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(("username", "role", "password_hash"))
    for role, password in _PASSWORDS.items():
        sheet.append((role, role, hash_password(password)))
    workbook.save(path)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    import api.auth as auth_module

    original_sessions = auth_module._sessions
    auth_module._sessions = SessionStore()

    credentials_file = tmp_path / "credentials.xlsx"
    _write_credentials(credentials_file)
    monkeypatch.setenv("CREDENTIALS_XLSX_PATH", str(credentials_file))

    with TestClient(app) as test_client:
        yield test_client

    auth_module._sessions = original_sessions


@pytest.mark.parametrize("role", _ROLES)
def test_login_succeeds_with_the_right_credentials_and_reports_the_role(
    client: TestClient, role: str
) -> None:
    resp = client.post("/api/v1/auth/login", json={"username": role, "password": _PASSWORDS[role]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["token"]
    assert body["role"] == role


@pytest.mark.parametrize("role", _ROLES)
def test_login_fails_with_the_wrong_password(client: TestClient, role: str) -> None:
    resp = client.post("/api/v1/auth/login", json={"username": role, "password": "wrong"})
    assert resp.status_code == 401


def test_login_fails_with_an_unknown_username(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/auth/login", json={"username": "someone-else", "password": "whatever"}
    )
    assert resp.status_code == 401


def test_login_always_fails_when_the_credentials_file_is_missing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CREDENTIALS_XLSX_PATH", str(tmp_path / "does-not-exist.xlsx"))
    resp = client.post("/api/v1/auth/login", json={"username": "ap", "password": _PASSWORDS["ap"]})
    assert resp.status_code == 401


def test_two_different_logins_issue_different_tokens(client: TestClient) -> None:
    login_body = {"username": "ap", "password": _PASSWORDS["ap"]}
    first = client.post("/api/v1/auth/login", json=login_body).json()["token"]
    second = client.post("/api/v1/auth/login", json=login_body).json()["token"]
    assert first != second


def test_logout_invalidates_the_token(client: TestClient) -> None:
    login = client.post("/api/v1/auth/login", json={"username": "ap", "password": _PASSWORDS["ap"]})
    token = login.json()["token"]

    first_logout = client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert first_logout.status_code == 204

    # The same token, used again, is no longer valid — proves logout actually revoked it
    # rather than just returning 204 unconditionally.
    second_logout = client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert second_logout.status_code == 401


def test_logout_without_a_session_is_a_401(client: TestClient) -> None:
    resp = client.post("/api/v1/auth/logout")
    assert resp.status_code == 401


def test_an_ap_login_and_an_admin_login_can_both_log_themselves_out(
    client: TestClient,
) -> None:
    # Not a separation to defend (see api/auth.py's docstring) — just confirms logout
    # works for either role, since it's one shared endpoint now.
    for role in _ROLES:
        token = client.post(
            "/api/v1/auth/login", json={"username": role, "password": _PASSWORDS[role]}
        ).json()["token"]
        resp = client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 204


def test_login_refuses_an_expired_account_with_a_distinct_message(
    client: TestClient, tmp_path: Path
) -> None:
    yesterday = (datetime.now(UTC).date() - timedelta(days=1)).isoformat()
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(_HEADER)
    sheet.append(("demo", "ap", hash_password("demo-pw"), "", 0, yesterday, "2026-01-01"))
    path = tmp_path / "expired.xlsx"
    workbook.save(path)

    import os

    original = os.environ.get("CREDENTIALS_XLSX_PATH")
    os.environ["CREDENTIALS_XLSX_PATH"] = str(path)
    try:
        resp = client.post("/api/v1/auth/login", json={"username": "demo", "password": "demo-pw"})
    finally:
        if original is not None:
            os.environ["CREDENTIALS_XLSX_PATH"] = original
        else:
            os.environ.pop("CREDENTIALS_XLSX_PATH", None)

    assert resp.status_code == 403
    assert "expired" in resp.json()["detail"].lower()
    # Distinct from a wrong-password 401 — the login page can tell the two apart.
    assert resp.status_code != 401


def test_login_still_fails_with_401_for_an_expired_account_and_the_wrong_password(
    client: TestClient, tmp_path: Path
) -> None:
    # Password checked before expiry — an expired account doesn't leak "this account
    # exists and is expired" to someone who doesn't even have the right password.
    yesterday = (datetime.now(UTC).date() - timedelta(days=1)).isoformat()
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(_HEADER)
    sheet.append(("demo", "ap", hash_password("demo-pw"), "", 0, yesterday, "2026-01-01"))
    path = tmp_path / "expired.xlsx"
    workbook.save(path)

    import os

    original = os.environ.get("CREDENTIALS_XLSX_PATH")
    os.environ["CREDENTIALS_XLSX_PATH"] = str(path)
    try:
        resp = client.post("/api/v1/auth/login", json={"username": "demo", "password": "wrong"})
    finally:
        if original is not None:
            os.environ["CREDENTIALS_XLSX_PATH"] = original
        else:
            os.environ.pop("CREDENTIALS_XLSX_PATH", None)

    assert resp.status_code == 401


def test_login_succeeds_for_an_account_that_is_valid_until_today(
    client: TestClient, tmp_path: Path
) -> None:
    today = datetime.now(UTC).date().isoformat()
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(_HEADER)
    sheet.append(("demo", "ap", hash_password("demo-pw"), "", 0, today, "2026-01-01"))
    path = tmp_path / "still-valid.xlsx"
    workbook.save(path)

    import os

    original = os.environ.get("CREDENTIALS_XLSX_PATH")
    os.environ["CREDENTIALS_XLSX_PATH"] = str(path)
    try:
        resp = client.post("/api/v1/auth/login", json={"username": "demo", "password": "demo-pw"})
    finally:
        if original is not None:
            os.environ["CREDENTIALS_XLSX_PATH"] = original
        else:
            os.environ.pop("CREDENTIALS_XLSX_PATH", None)

    assert resp.status_code == 200
