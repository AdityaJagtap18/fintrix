"""Claim / release / submit — the exclusivity logic behind US-03 ("two clerks can't work
the same invoice"). Every function here takes a `ReviewAssignmentRepository` and does
nothing else with the outside world; the FastAPI layer (Phase 3) is a thin translation
from these typed exceptions to HTTP status codes, not where the actual decision is made.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from core.models import ReviewAssignment, ReviewOutcome
from core.persistence import ReviewAssignmentRepository

DEFAULT_LOCK_DURATION_MINUTES = 30


class ReviewError(Exception):
    """Base for review-workflow errors — always a specific subclass, never a bare message,
    so the API layer can map each to the right HTTP status without parsing text."""


class AlreadyClaimedError(ReviewError):
    def __init__(self, invoice_id: str, held_by: str):
        self.invoice_id = invoice_id
        self.held_by = held_by
        super().__init__(f"invoice {invoice_id} is already claimed by {held_by}")


class NotClaimedByReviewerError(ReviewError):
    def __init__(self, invoice_id: str, reviewer: str):
        self.invoice_id = invoice_id
        self.reviewer = reviewer
        super().__init__(f"{reviewer} does not hold an active claim on invoice {invoice_id}")


def claim(
    invoice_id: str,
    reviewer: str,
    now: datetime,
    repo: ReviewAssignmentRepository,
    *,
    lock_duration: timedelta = timedelta(minutes=DEFAULT_LOCK_DURATION_MINUTES),
) -> ReviewAssignment:
    """AC-03.1: a second claim while one is active is rejected, naming the current holder.
    An already-expired claim is treated as available immediately — a claim attempt
    shouldn't have to wait for the next sweep (`sweep_expired_locks`) to notice."""
    existing = repo.get(invoice_id)
    if (
        existing is not None
        and existing.claimed_by is not None
        and existing.is_claimed
        and not existing.is_expired(now)
    ):
        raise AlreadyClaimedError(invoice_id, existing.claimed_by)

    assignment = ReviewAssignment(
        invoice_id=invoice_id,
        claimed_by=reviewer,
        claimed_at=now,
        expires_at=now + lock_duration,
    )
    repo.save(assignment)
    return assignment


def release(
    invoice_id: str, reviewer: str, now: datetime, repo: ReviewAssignmentRepository
) -> ReviewAssignment:
    _require_active_claim(invoice_id, reviewer, now, repo)
    released = ReviewAssignment(invoice_id=invoice_id)
    repo.save(released)
    return released


def submit(
    invoice_id: str,
    reviewer: str,
    outcome: ReviewOutcome,
    now: datetime,
    repo: ReviewAssignmentRepository,
) -> ReviewAssignment:
    """AC-03.3: a reviewer without the (still-active) lock cannot submit."""
    existing = _require_active_claim(invoice_id, reviewer, now, repo)
    submitted = ReviewAssignment(
        invoice_id=invoice_id,
        claimed_by=existing.claimed_by,
        claimed_at=existing.claimed_at,
        expires_at=existing.expires_at,
        submitted_at=now,
        outcome=outcome,
    )
    repo.save(submitted)
    return submitted


def sweep_expired_locks(now: datetime, repo: ReviewAssignmentRepository) -> list[str]:
    """AC-03.2: every claim past its lock window with no submission returns to the queue.
    Returns the affected invoice IDs — the caller is expected to write one audit record
    per ID (Article VIII); this function's job is only to decide which ones qualify."""
    released_ids = []
    for assignment in repo.find_expired(now):
        repo.save(ReviewAssignment(invoice_id=assignment.invoice_id))
        released_ids.append(assignment.invoice_id)
    return released_ids


def _require_active_claim(
    invoice_id: str, reviewer: str, now: datetime, repo: ReviewAssignmentRepository
) -> ReviewAssignment:
    existing = repo.get(invoice_id)
    if (
        existing is None
        or not existing.is_claimed
        or existing.is_expired(now)
        or existing.claimed_by != reviewer
    ):
        raise NotClaimedByReviewerError(invoice_id, reviewer)
    return existing
