"""Dependency injection wiring for the FastAPI app.

Two things here are explicit placeholders, not real implementations, and both are called
out at the point they're used so nobody mistakes them for finished work:

* `InMemoryReviewAssignmentRepository` — satisfies `ReviewAssignmentRepository`
  structurally so the API layer and its tests can run today, but state doesn't survive a
  restart and there's no real concurrency control. T-047 (SQLAlchemy/Azure SQL
  repositories) replaces this; nothing above `get_review_repo()` needs to change when it
  does, since routers depend on the Protocol, not this class.
* `get_current_reviewer` — reads an `X-Reviewer` header instead of validating an Entra ID
  bearer token. T-062 replaces this with real JWT validation (NFR-021/022). Every caller
  of this dependency needs revisiting before this serves anything but a laptop.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import Header, HTTPException, status

from core.models import ReviewAssignment
from core.persistence import ReviewAssignmentRepository


def now_utc() -> datetime:
    """This system's datetime convention: naive, but always UTC. Mixing this with a
    timezone-aware value anywhere will raise on comparison — that's deliberate, it's
    cheaper to catch here than to debug a silent timezone bug in `is_expired`."""
    return datetime.now(UTC).replace(tzinfo=None)


class InMemoryReviewAssignmentRepository:
    """Dev/test placeholder for `ReviewAssignmentRepository` — see module docstring."""

    def __init__(self) -> None:
        self._store: dict[str, ReviewAssignment] = {}

    def get(self, invoice_id: str) -> ReviewAssignment | None:
        return self._store.get(invoice_id)

    def save(self, assignment: ReviewAssignment) -> None:
        self._store[assignment.invoice_id] = assignment

    def find_expired(self, now: datetime) -> list[ReviewAssignment]:
        return [a for a in self._store.values() if a.is_expired(now)]


_review_repo = InMemoryReviewAssignmentRepository()


def get_review_repo() -> ReviewAssignmentRepository:
    return _review_repo


def get_current_reviewer(x_reviewer: Annotated[str | None, Header()] = None) -> str:
    if not x_reviewer:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-Reviewer header required (dev auth placeholder pending T-062)",
        )
    return x_reviewer
