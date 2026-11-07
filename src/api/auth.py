"""Simple, real, backend-verified login for anyone using this app. One sign-in gates the
whole thing (Processing Results, Review, Preprocessing, Accepted) — get_session is a
router-level dependency on every gated router now, not an opt-in per endpoint. One extra
role ("admin") unlocks the Admin tab (forwarded queue, accept, export) on top of that —
get_admin_session checks the session's role on top of what get_session already checks. A
third role ("superadmin") is its own separate thing entirely — not a superset of admin —
with its own dashboard (api/routers/superadmin.py) for creating demo accounts and setting
their document quota / validity window.

Not Entra ID: every user — including quota/expiry — lives in a spreadsheet (see
scripts/manage_credentials.py), not in .env or a real database. That's a deliberate
demo-scale choice: a spreadsheet is a format someone can inspect and hand-edit without
touching code, and this app has never needed more than a handful of accounts. Every
password is bcrypt-hashed at rest; nothing here ever stores or compares one in plaintext.
Genuinely enforced (bcrypt's own constant-time comparison, a decoy check on an unknown
username so a bad username doesn't respond measurably faster than a bad password, real
session tokens with a real expiry) — just not tied to an Azure identity provider, which
this app has never needed for its own end users.

One session store: a token carries the role AND username it was issued for. Role checks
(get_admin_session, get_superadmin_session) are strictly additive on top of get_session,
not parallel gates — an admin can do everything AP can, plus accept/export; a superadmin
is its own island (see api/routers/superadmin.py's own docstring for why it isn't just
"admin plus more"). The username is what lets a quota be *per person* rather than global.
"""

from __future__ import annotations

import os
import secrets
import threading
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import Annotated

import bcrypt
from fastapi import Header, HTTPException, status
from openpyxl import Workbook, load_workbook

_SESSION_TTL = timedelta(hours=8)
_DEFAULT_CREDENTIALS_PATH = "data/credentials.xlsx"
_HEADER = (
    "username",
    "role",
    "password_hash",
    "doc_quota",
    "docs_used",
    "valid_until",
    "created_at",
)


@dataclass(frozen=True)
class UserRecord:
    username: str
    role: str
    password_hash: str
    doc_quota: int | None  # None = unlimited
    docs_used: int
    valid_until: date | None  # None = never expires
    created_at: date | None


class SessionStore:
    """Process-lifetime only, same in-memory convention as processing.py's
    _ProcessingStore — sessions don't survive a restart, which is fine: signing in again
    costs nothing a real user notices."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # token -> (issued_at, role, username)
        self._sessions: dict[str, tuple[datetime, str, str]] = {}

    def issue(self, role: str, username: str) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[token] = (datetime.now(UTC), role, username)
        return token

    def identity_for(self, token: str) -> tuple[str, str] | None:
        """(role, username) for a still-valid token, or None if missing/expired."""
        with self._lock:
            entry = self._sessions.get(token)
            if entry is None:
                return None
            issued_at, role, username = entry
            if datetime.now(UTC) - issued_at > _SESSION_TTL:
                del self._sessions[token]
                return None
            return (role, username)

    def revoke(self, token: str) -> None:
        with self._lock:
            self._sessions.pop(token, None)


_sessions = SessionStore()

# Guards the credentials workbook's read-modify-write cycles (record_documents_used,
# upsert_user) against two requests racing each other into a corrupt file. Login and
# plain reads (load_users) don't need it — they only ever read.
_workbook_lock = threading.Lock()


def credentials_path() -> str:
    return os.environ.get("CREDENTIALS_XLSX_PATH", _DEFAULT_CREDENTIALS_PATH)


def hash_password(password: str) -> str:
    """The one place a plaintext password becomes what's actually persisted — bcrypt
    salts and hashes it. Used both by real logins (comparing against a stored hash) and
    scripts/manage_credentials.py / the superadmin create-user endpoint (writing one);
    nothing else in this codebase should call bcrypt directly."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def generate_password() -> str:
    """A real random password for a superadmin-created account that didn't get one typed
    in — shown to the superadmin exactly once in the create-user response; never logged,
    never stored anywhere but as its bcrypt hash."""
    return secrets.token_urlsafe(12)


def _parse_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return int(str(value))


def _parse_date(value: object) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def load_users() -> dict[str, UserRecord]:
    """username -> UserRecord for every row in the credentials workbook. Read fresh on
    every call, not cached — someone editing the spreadsheet (or another request
    updating docs_used) while the server is running takes effect immediately, no
    restart. Tolerant of the older 3-column (username, role, password_hash) shape —
    openpyxl still yields the missing trailing cells as None when read up to max_col=7,
    so an old-format row just comes back with doc_quota/valid_until/created_at unset
    (unlimited, never expires) rather than failing to parse.
    """
    path = credentials_path()
    if not os.path.exists(path):
        return {}
    workbook = load_workbook(path, read_only=True, data_only=True)
    sheet = workbook.active
    if sheet is None:
        return {}
    users: dict[str, UserRecord] = {}
    for row in sheet.iter_rows(min_row=2, max_col=7, values_only=True):
        if row is None or len(row) < 3:
            continue
        username, role, password_hash = row[0], row[1], row[2]
        if username is None or role is None or password_hash is None:
            continue
        doc_quota = _parse_int(row[3]) if len(row) > 3 else None
        docs_used = _parse_int(row[4]) if len(row) > 4 else None
        valid_until = _parse_date(row[5]) if len(row) > 5 else None
        created_at = _parse_date(row[6]) if len(row) > 6 else None
        users[str(username)] = UserRecord(
            username=str(username),
            role=str(role).strip().lower(),
            password_hash=str(password_hash),
            doc_quota=doc_quota,
            docs_used=docs_used or 0,
            valid_until=valid_until,
            created_at=created_at,
        )
    return users


def _write_users(users: dict[str, UserRecord]) -> None:
    """Rewrites the whole sheet from `users` — the workbook is small (a handful of demo
    accounts), so a full rewrite on every change is simpler and safer than trying to
    patch individual cells. Caller must hold _workbook_lock."""
    path = credentials_path()
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    workbook = Workbook()
    sheet = workbook.active
    if sheet is None:  # pragma: no cover - openpyxl always gives a fresh Workbook one
        raise RuntimeError("Workbook() has no active worksheet — should be unreachable")
    sheet.title = "credentials"
    sheet.append(_HEADER)
    for record in users.values():
        sheet.append(
            (
                record.username,
                record.role,
                record.password_hash,
                record.doc_quota if record.doc_quota is not None else "",
                record.docs_used,
                record.valid_until.isoformat() if record.valid_until else "",
                record.created_at.isoformat() if record.created_at else "",
            )
        )
    workbook.save(path)


def get_user(username: str) -> UserRecord | None:
    return load_users().get(username)


def upsert_user(record: UserRecord) -> None:
    """Adds `record` or replaces the existing row for the same username."""
    with _workbook_lock:
        users = load_users()
        users[record.username] = record
        _write_users(users)


def delete_user(username: str) -> bool:
    """Removes `username`'s row entirely. Returns whether there was one to remove."""
    with _workbook_lock:
        users = load_users()
        if username not in users:
            return False
        del users[username]
        _write_users(users)
        return True


def record_documents_used(username: str, count: int) -> None:
    """Adds `count` to `username`'s running total — called once a real extraction
    genuinely succeeds (see api/routers/processing.py / preprocessing.py), never for an
    attempt that errored out. A no-op if the user row has since been removed."""
    if count <= 0:
        return
    with _workbook_lock:
        users = load_users()
        record = users.get(username)
        if record is None:
            return
        users[username] = replace(record, docs_used=record.docs_used + count)
        _write_users(users)


def is_expired(record: UserRecord, *, today: date) -> bool:
    return record.valid_until is not None and record.valid_until < today


def remaining_quota(record: UserRecord) -> int | None:
    """None means unlimited."""
    if record.doc_quota is None:
        return None
    return max(0, record.doc_quota - record.docs_used)


def ensure_quota_available(username: str, needed: int) -> None:
    """Raises a 403 if `username` doesn't have `needed` documents left in their quota —
    called *before* a real extraction starts, so a batch that would blow the budget never
    partially runs. A missing user row (session outlived a deleted account) is a 401, not
    a quota problem."""
    record = get_user(username)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="session expired or invalid — sign in again",
        )
    remaining = remaining_quota(record)
    if remaining is not None and needed > remaining:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Demo document limit reached ({record.docs_used}/{record.doc_quota} "
                "used) — ask your admin for more."
            ),
        )


_decoy_hash_lock = threading.Lock()
_decoy_hash: bytes | None = None


def _get_decoy_hash() -> bytes:
    # A single fixed decoy, computed once and reused — bcrypt.checkpw against *some*
    # hash is what closes the timing gap between "unknown username" and "known username,
    # wrong password"; generating a fresh salt per failed attempt would just be wasted
    # cost with no benefit.
    global _decoy_hash
    with _decoy_hash_lock:
        if _decoy_hash is None:
            _decoy_hash = bcrypt.hashpw(b"decoy-password-never-matches", bcrypt.gensalt())
        return _decoy_hash


def verify_credentials(username: str, password: str) -> UserRecord | None:
    """bcrypt-verifies only; does NOT check expiry — api/routers/auth.py's login handler
    checks that separately, so it can tell "wrong password" apart from "right password,
    expired account" and show a different message for each."""
    record = load_users().get(username)
    if record is None:
        bcrypt.checkpw(password.encode("utf-8"), _get_decoy_hash())
        return None
    if bcrypt.checkpw(password.encode("utf-8"), record.password_hash.encode("utf-8")):
        return record
    return None


def issue_session(role: str, username: str) -> str:
    return _sessions.issue(role, username)


def revoke_session(token: str) -> None:
    _sessions.revoke(token)


def _bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="sign-in required")
    return authorization.removeprefix("Bearer ")


def get_session(authorization: Annotated[str | None, Header()] = None) -> str:
    """FastAPI dependency: every gated router requires a valid
    `Authorization: Bearer <token>` header from a prior POST /auth/login — any role
    satisfies this, AP, admin, or superadmin."""
    token = _bearer_token(authorization)
    if _sessions.identity_for(token) is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="session expired or invalid — sign in again",
        )
    return token


def _require_role(authorization: str | None, role: str, *, forbidden_detail: str) -> str:
    token = _bearer_token(authorization)
    identity = _sessions.identity_for(token)
    if identity is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="session expired or invalid — sign in again",
        )
    actual_role, _username = identity
    if actual_role != role:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=forbidden_detail)
    return token


def get_admin_session(authorization: Annotated[str | None, Header()] = None) -> str:
    """Same as get_session, plus: the token must have been issued to an admin-role user.
    A valid AP session satisfies get_session everywhere but never satisfies this — 403,
    not 401, since the session itself is real, it just isn't admin."""
    return _require_role(authorization, "admin", forbidden_detail="admin sign-in required")


def get_superadmin_session(authorization: Annotated[str | None, Header()] = None) -> str:
    """Gates api/routers/superadmin.py — a superadmin session is not an admin session and
    vice versa (see this module's docstring: superadmin is its own island, not "admin
    plus more")."""
    return _require_role(
        authorization, "superadmin", forbidden_detail="superadmin sign-in required"
    )


def get_current_username(authorization: Annotated[str | None, Header()] = None) -> str:
    """For the handful of endpoints that need to know *who*, not just *whether* — the
    real-extraction endpoints, to check/record a per-user document quota."""
    token = _bearer_token(authorization)
    identity = _sessions.identity_for(token)
    if identity is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="session expired or invalid — sign in again",
        )
    _role, username = identity
    return username
