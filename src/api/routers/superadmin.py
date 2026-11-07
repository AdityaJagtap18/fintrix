"""Superadmin endpoints: list every user with their usage/expiry, and create new demo
accounts (AP or admin) with an optional document quota and validity window.

Superadmin is deliberately its own island, not "admin plus more" — this router only ever
manages accounts, it doesn't touch invoices, and nothing in api/routers/admin.py accepts
a superadmin session. A superadmin who also needs to process invoices signs in twice,
once per role, the same as anyone else with two accounts. That keeps the one dashboard
this role gets ("only all the stats", per how this was asked for) genuinely small — user
management, not a backdoor into everything else.

Every password handed back here is real plaintext, shown exactly once in the create-user
response — bcrypt only ever stores the hash (api/auth.py's hash_password), so there is no
way to retrieve it again after this response. The superadmin is responsible for passing it
on to whoever the account is for.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from api.auth import (
    UserRecord,
    delete_user,
    generate_password,
    get_current_username,
    get_superadmin_session,
    get_user,
    hash_password,
    is_expired,
    load_users,
    upsert_user,
)

router = APIRouter(
    prefix="/api/v1/superadmin",
    tags=["superadmin"],
    dependencies=[Depends(get_superadmin_session)],
)

_CREATABLE_ROLES = ("ap", "admin")


class UserSummaryDto(BaseModel):
    username: str
    role: str
    doc_quota: int | None
    docs_used: int
    valid_until: str | None
    created_at: str | None
    status: str  # "active" | "expired"


def _summary(record: UserRecord, *, today: date) -> UserSummaryDto:
    return UserSummaryDto(
        username=record.username,
        role=record.role,
        doc_quota=record.doc_quota,
        docs_used=record.docs_used,
        valid_until=record.valid_until.isoformat() if record.valid_until else None,
        created_at=record.created_at.isoformat() if record.created_at else None,
        status="expired" if is_expired(record, today=today) else "active",
    )


@router.get("/users", response_model=list[UserSummaryDto])
def list_users() -> list[UserSummaryDto]:
    today = datetime.now(UTC).date()
    users = sorted(load_users().values(), key=lambda u: u.username)
    return [_summary(u, today=today) for u in users]


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=1)
    role: str
    password: str | None = None
    doc_quota: int | None = None
    valid_days: int | None = None


class CreateUserResponse(BaseModel):
    username: str
    password: str
    role: str
    doc_quota: int | None
    valid_until: str | None


@router.post("/users", response_model=CreateUserResponse, status_code=status.HTTP_201_CREATED)
def create_user(request: CreateUserRequest) -> CreateUserResponse:
    if request.role not in _CREATABLE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"role must be one of {_CREATABLE_ROLES!r}",
        )
    if get_user(request.username) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"a user named {request.username!r} already exists",
        )
    if request.doc_quota is not None and request.doc_quota < 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="doc_quota can't be negative",
        )
    if request.valid_days is not None and request.valid_days <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="valid_days must be a positive number of days",
        )

    password = request.password or generate_password()
    today = datetime.now(UTC).date()
    valid_until = today + timedelta(days=request.valid_days) if request.valid_days else None
    record = UserRecord(
        username=request.username,
        role=request.role,
        password_hash=hash_password(password),
        doc_quota=request.doc_quota,
        docs_used=0,
        valid_until=valid_until,
        created_at=today,
    )
    upsert_user(record)
    return CreateUserResponse(
        username=record.username,
        password=password,
        role=record.role,
        doc_quota=record.doc_quota,
        valid_until=valid_until.isoformat() if valid_until else None,
    )


class ChangePasswordRequest(BaseModel):
    # Same "type one or leave it blank to generate one" pattern as CreateUserRequest.
    password: str | None = None


class ChangePasswordResponse(BaseModel):
    username: str
    password: str


@router.post("/users/{username}/password", response_model=ChangePasswordResponse)
def change_password(username: str, request: ChangePasswordRequest) -> ChangePasswordResponse:
    """Resets one user's password — nothing else. Role, quota, docs_used, and expiry are
    all carried over unchanged (see scripts/manage_credentials.py's own cmd_add for why a
    password reset silently touching those would be a real footgun, not a feature)."""
    record = get_user(username)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"no user named {username!r}"
        )
    password = request.password or generate_password()
    upsert_user(replace(record, password_hash=hash_password(password)))
    return ChangePasswordResponse(username=username, password=password)


@router.delete("/users/{username}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user_account(
    username: str, current_username: Annotated[str, Depends(get_current_username)]
) -> None:
    """Removes a user's row entirely — any role, not just AP/admin (the table shows every
    account, so this can act on any of them), except the one the caller is currently
    signed in as: deleting your own row wouldn't kick you out of *this* session (sessions
    aren't re-checked against the credentials file per request), but it would strand you
    the moment you signed out, with no easy way back in short of the CLI. Refusing it here
    is cheap insurance against that."""
    if username == current_username:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="you can't delete the account you're currently signed in as",
        )
    if not delete_user(username):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"no user named {username!r}"
        )
