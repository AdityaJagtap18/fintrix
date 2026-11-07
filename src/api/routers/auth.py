"""POST /auth/login and /logout — one sign-in for the whole app. Session/credential logic
itself lives in api/auth.py; this router is just the HTTP surface over it. The response
carries the signed-in user's role ("ap", "admin", or "superadmin") so the frontend knows
which UI to show — nothing about what the token can actually reach depends on the
frontend getting that right, though; api/auth.py's get_admin_session/
get_superadmin_session check the role again, server-side, on every gated request
regardless of what the UI shows.

A right password on an expired demo account is refused here too — with a distinct
message from a wrong password, so the login page itself can tell someone their demo has
ended rather than making them guess.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from api.auth import get_session, is_expired, issue_session, revoke_session, verify_credentials

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    role: str


@router.post("/login", response_model=LoginResponse)
def login(request: LoginRequest) -> LoginResponse:
    record = verify_credentials(request.username, request.password)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="incorrect username or password"
        )
    if is_expired(record, today=datetime.now(UTC).date()):
        if record.valid_until is None:  # pragma: no cover - is_expired() guarantees this
            raise RuntimeError("is_expired() returned True with no valid_until set")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"This demo account expired on {record.valid_until.isoformat()} — "
                "ask your admin for a new one."
            ),
        )
    return LoginResponse(token=issue_session(record.role, record.username), role=record.role)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(token: Annotated[str, Depends(get_session)]) -> None:
    revoke_session(token)
