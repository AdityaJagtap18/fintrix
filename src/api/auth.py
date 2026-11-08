"""Real (if minimal) credential check and bearer-token issuance for the FINTRIX login
screen. This is deliberately not T-062's Entra ID/JWT integration — that needs a tenant to
federate against and is its own large piece of work — but it is a genuine check against a
seeded user store, a real random opaque token, and a real rejection path. No login ever
reports success without a matching password on file.

`get_current_reviewer` in `dependencies.py` (the `X-Reviewer` header placeholder used by
the claim/release/submit endpoints) is untouched — the frontend derives that header's
value from the authenticated user's name after a real login here, rather than this module
replacing that mechanism outright.
"""

from __future__ import annotations

import secrets

from api.seed_data import SEED_USERS, SeedUser

_USERS_BY_EMAIL: dict[str, SeedUser] = {u.email.lower(): u for u in SEED_USERS}
_TOKENS: dict[str, str] = {}  # token -> email


class InvalidCredentialsError(Exception):
    pass


def login(email: str, password: str) -> tuple[str, SeedUser]:
    user = _USERS_BY_EMAIL.get(email.strip().lower())
    if user is None or not secrets.compare_digest(user.password, password):
        raise InvalidCredentialsError("invalid email or password")
    token = secrets.token_urlsafe(32)
    _TOKENS[token] = user.email
    return token, user


def user_for_token(token: str) -> SeedUser | None:
    email = _TOKENS.get(token)
    return _USERS_BY_EMAIL.get(email) if email else None


def logout(token: str) -> None:
    _TOKENS.pop(token, None)
