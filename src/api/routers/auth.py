"""Login endpoint for the FINTRIX UI (see `api.auth` module docstring for scope)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel

from api.auth import InvalidCredentialsError, user_for_token
from api.auth import login as do_login

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


class UserResponse(BaseModel):
    email: str
    name: str
    role: str


class LoginResponse(BaseModel):
    token: str
    user: UserResponse


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest) -> LoginResponse:
    try:
        token, user = do_login(body.email, body.password)
    except InvalidCredentialsError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    user_response = UserResponse(email=user.email, name=user.name, role=user.role)
    return LoginResponse(token=token, user=user_response)


@router.get("/me", response_model=UserResponse)
def me(authorization: Annotated[str | None, Header()] = None) -> UserResponse:
    token = (authorization or "").removeprefix("Bearer ").strip()
    user = user_for_token(token) if token else None
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
    return UserResponse(email=user.email, name=user.name, role=user.role)
