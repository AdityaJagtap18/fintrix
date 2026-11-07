"""HTTP-level tests for the superadmin endpoints: listing users with their usage/expiry,
and creating new AP/admin demo accounts with an optional quota and validity window.
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

_SUPERADMIN_PASSWORD = "top-of-the-house"
_HEADER = (
    "username",
    "role",
    "password_hash",
    "doc_quota",
    "docs_used",
    "valid_until",
    "created_at",
)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    import api.auth as auth_module

    original_sessions = auth_module._sessions
    auth_module._sessions = SessionStore()

    credentials_file = tmp_path / "credentials.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(_HEADER)
    sheet.append(
        ("root", "superadmin", hash_password(_SUPERADMIN_PASSWORD), "", 0, "", "2026-01-01")
    )
    sheet.append(("ap", "ap", hash_password("ap-pw"), "", 0, "", "2026-01-01"))
    workbook.save(credentials_file)
    monkeypatch.setenv("CREDENTIALS_XLSX_PATH", str(credentials_file))

    with TestClient(app) as test_client:
        yield test_client

    auth_module._sessions = original_sessions


def _superadmin_token(client: TestClient) -> str:
    resp = client.post(
        "/api/v1/auth/login", json={"username": "root", "password": _SUPERADMIN_PASSWORD}
    )
    assert resp.status_code == 200, resp.text
    token: str = resp.json()["token"]
    return token


def _superadmin_headers(client: TestClient) -> dict[str, str]:
    return {"Authorization": f"Bearer {_superadmin_token(client)}"}


def test_users_requires_a_session(client: TestClient) -> None:
    resp = client.get("/api/v1/superadmin/users")
    assert resp.status_code == 401


def test_users_rejects_a_valid_non_superadmin_session_with_403(client: TestClient) -> None:
    login = client.post("/api/v1/auth/login", json={"username": "ap", "password": "ap-pw"})
    token = login.json()["token"]
    resp = client.get("/api/v1/superadmin/users", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


def test_lists_every_seeded_user(client: TestClient) -> None:
    resp = client.get("/api/v1/superadmin/users", headers=_superadmin_headers(client))
    assert resp.status_code == 200
    usernames = {u["username"] for u in resp.json()}
    assert usernames == {"root", "ap"}


def test_creates_a_user_with_a_typed_password(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/superadmin/users",
        headers=_superadmin_headers(client),
        json={"username": "priya", "role": "ap", "password": "hand-picked-pw"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["username"] == "priya"
    assert body["password"] == "hand-picked-pw"
    assert body["doc_quota"] is None
    assert body["valid_until"] is None

    # And it's real — priya can actually sign in with it.
    login = client.post(
        "/api/v1/auth/login", json={"username": "priya", "password": "hand-picked-pw"}
    )
    assert login.status_code == 200
    assert login.json()["role"] == "ap"


def test_creates_a_user_with_a_generated_password_when_none_is_given(
    client: TestClient,
) -> None:
    resp = client.post(
        "/api/v1/superadmin/users",
        headers=_superadmin_headers(client),
        json={"username": "rajesh", "role": "admin"},
    )
    assert resp.status_code == 201
    body = resp.json()
    generated_password = body["password"]
    assert generated_password  # a real, non-empty password came back

    login = client.post(
        "/api/v1/auth/login", json={"username": "rajesh", "password": generated_password}
    )
    assert login.status_code == 200
    assert login.json()["role"] == "admin"


def test_creates_a_user_with_a_quota_and_a_validity_window(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/superadmin/users",
        headers=_superadmin_headers(client),
        json={
            "username": "demo1",
            "role": "ap",
            "password": "pw",
            "doc_quota": 10,
            "valid_days": 7,
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["doc_quota"] == 10
    expected_expiry = (datetime.now(UTC).date() + timedelta(days=7)).isoformat()
    assert body["valid_until"] == expected_expiry

    listed = client.get("/api/v1/superadmin/users", headers=_superadmin_headers(client)).json()
    demo1 = next(u for u in listed if u["username"] == "demo1")
    assert demo1["doc_quota"] == 10
    assert demo1["docs_used"] == 0
    assert demo1["status"] == "active"


def test_rejects_a_duplicate_username(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/superadmin/users",
        headers=_superadmin_headers(client),
        json={"username": "ap", "role": "ap", "password": "whatever"},
    )
    assert resp.status_code == 409


def test_rejects_an_invalid_role(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/superadmin/users",
        headers=_superadmin_headers(client),
        json={"username": "someone", "role": "superadmin", "password": "whatever"},
    )
    assert resp.status_code == 422


def test_a_user_created_with_an_already_past_valid_days_is_impossible_but_zero_is_rejected(
    client: TestClient,
) -> None:
    resp = client.post(
        "/api/v1/superadmin/users",
        headers=_superadmin_headers(client),
        json={"username": "demo2", "role": "ap", "password": "pw", "valid_days": 0},
    )
    assert resp.status_code == 422


def test_lists_an_expired_user_as_expired(client: TestClient, tmp_path: Path) -> None:
    # Seed an already-expired row directly (valid_days must be positive through the API,
    # so this exercises the *listing* side of expiry, not creation).

    yesterday = (datetime.now(UTC).date() - timedelta(days=1)).isoformat()
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(_HEADER)
    sheet.append(
        ("root", "superadmin", hash_password(_SUPERADMIN_PASSWORD), "", 0, "", "2026-01-01")
    )
    sheet.append(("stale", "ap", hash_password("pw"), "", 0, yesterday, "2026-01-01"))
    path = tmp_path / "expired.xlsx"
    workbook.save(path)

    import os

    original_path = os.environ.get("CREDENTIALS_XLSX_PATH")
    os.environ["CREDENTIALS_XLSX_PATH"] = str(path)
    try:
        resp = client.get("/api/v1/superadmin/users", headers=_superadmin_headers(client))
    finally:
        if original_path is not None:
            os.environ["CREDENTIALS_XLSX_PATH"] = original_path
        else:
            os.environ.pop("CREDENTIALS_XLSX_PATH", None)

    stale = next(u for u in resp.json() if u["username"] == "stale")
    assert stale["status"] == "expired"


def test_change_password_requires_a_session(client: TestClient) -> None:
    resp = client.post("/api/v1/superadmin/users/ap/password", json={})
    assert resp.status_code == 401


def test_change_password_rejects_a_valid_non_superadmin_session_with_403(
    client: TestClient,
) -> None:
    login = client.post("/api/v1/auth/login", json={"username": "ap", "password": "ap-pw"})
    token = login.json()["token"]
    resp = client.post(
        "/api/v1/superadmin/users/ap/password",
        headers={"Authorization": f"Bearer {token}"},
        json={},
    )
    assert resp.status_code == 403


def test_change_password_with_a_typed_password_and_signs_in_with_it(
    client: TestClient,
) -> None:
    resp = client.post(
        "/api/v1/superadmin/users/ap/password",
        headers=_superadmin_headers(client),
        json={"password": "brand-new-pw"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["username"] == "ap"
    assert body["password"] == "brand-new-pw"

    old_login = client.post("/api/v1/auth/login", json={"username": "ap", "password": "ap-pw"})
    assert old_login.status_code == 401

    new_login = client.post(
        "/api/v1/auth/login", json={"username": "ap", "password": "brand-new-pw"}
    )
    assert new_login.status_code == 200
    assert new_login.json()["role"] == "ap"


def test_change_password_generates_one_when_none_is_given(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/superadmin/users/ap/password",
        headers=_superadmin_headers(client),
        json={},
    )
    assert resp.status_code == 200
    generated_password = resp.json()["password"]
    assert generated_password

    login = client.post(
        "/api/v1/auth/login", json={"username": "ap", "password": generated_password}
    )
    assert login.status_code == 200


def test_change_password_leaves_role_quota_and_expiry_untouched(client: TestClient) -> None:
    create = client.post(
        "/api/v1/superadmin/users",
        headers=_superadmin_headers(client),
        json={
            "username": "demo1",
            "role": "ap",
            "password": "pw",
            "doc_quota": 10,
            "valid_days": 7,
        },
    )
    assert create.status_code == 201
    original = create.json()

    resp = client.post(
        "/api/v1/superadmin/users/demo1/password",
        headers=_superadmin_headers(client),
        json={"password": "a-fresh-password"},
    )
    assert resp.status_code == 200

    listed = client.get("/api/v1/superadmin/users", headers=_superadmin_headers(client)).json()
    demo1 = next(u for u in listed if u["username"] == "demo1")
    assert demo1["role"] == "ap"
    assert demo1["doc_quota"] == 10
    assert demo1["valid_until"] == original["valid_until"]


def test_change_password_for_an_unknown_user_is_a_404(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/superadmin/users/does-not-exist/password",
        headers=_superadmin_headers(client),
        json={},
    )
    assert resp.status_code == 404


def test_delete_requires_a_session(client: TestClient) -> None:
    resp = client.delete("/api/v1/superadmin/users/ap")
    assert resp.status_code == 401


def test_delete_rejects_a_valid_non_superadmin_session_with_403(client: TestClient) -> None:
    login = client.post("/api/v1/auth/login", json={"username": "ap", "password": "ap-pw"})
    token = login.json()["token"]
    resp = client.delete(
        "/api/v1/superadmin/users/ap", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 403


def test_delete_removes_the_user_and_they_can_no_longer_sign_in(client: TestClient) -> None:
    resp = client.delete("/api/v1/superadmin/users/ap", headers=_superadmin_headers(client))
    assert resp.status_code == 204

    listed = client.get("/api/v1/superadmin/users", headers=_superadmin_headers(client)).json()
    assert "ap" not in {u["username"] for u in listed}

    login = client.post("/api/v1/auth/login", json={"username": "ap", "password": "ap-pw"})
    assert login.status_code == 401


def test_delete_an_unknown_user_is_a_404(client: TestClient) -> None:
    resp = client.delete(
        "/api/v1/superadmin/users/does-not-exist", headers=_superadmin_headers(client)
    )
    assert resp.status_code == 404


def test_delete_refuses_to_remove_the_currently_signed_in_account(client: TestClient) -> None:
    resp = client.delete("/api/v1/superadmin/users/root", headers=_superadmin_headers(client))
    assert resp.status_code == 400

    listed = client.get("/api/v1/superadmin/users", headers=_superadmin_headers(client)).json()
    assert "root" in {u["username"] for u in listed}
