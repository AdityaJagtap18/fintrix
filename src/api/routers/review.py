"""Review claim/release/submit endpoints (plan.md §6; FR-061, FR-065; AC-03.1..03.3).

A thin translation layer only: every decision is made in `core.review.assignment`, and
this router's only real job is mapping typed exceptions to HTTP status codes.

Every endpoint here requires a session (this router's own dependencies=[Depends(
get_session)], api/auth.py) — any signed-in user, AP or admin, can review. AP (as the
reviewer role) no longer has final accept authority: submit only accepts REJECTED or
FORWARDED. FORWARDED lands in the admin queue (api/routers/admin.py); only an admin
accepting it turns it into APPROVED.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from api.auth import get_session
from api.dependencies import get_current_reviewer, get_review_repo, now_utc
from core.models import ReviewOutcome
from core.persistence import ReviewAssignmentRepository
from core.review.assignment import AlreadyClaimedError, NotClaimedByReviewerError
from core.review.assignment import claim as claim_invoice
from core.review.assignment import release as release_invoice
from core.review.assignment import submit as submit_invoice

router = APIRouter(prefix="/api/v1/review", tags=["review"], dependencies=[Depends(get_session)])

Reviewer = Annotated[str, Depends(get_current_reviewer)]
Repo = Annotated[ReviewAssignmentRepository, Depends(get_review_repo)]


class ClaimResponse(BaseModel):
    invoice_id: str
    claimed_by: str
    expires_at: datetime


class SubmitRequest(BaseModel):
    outcome: ReviewOutcome


class AssignmentResponse(BaseModel):
    invoice_id: str
    claimed_by: str | None
    submitted_at: datetime | None
    outcome: ReviewOutcome | None


@router.post("/{invoice_id}/claim", response_model=ClaimResponse)
def claim(invoice_id: str, reviewer: Reviewer, repo: Repo) -> ClaimResponse:
    try:
        assignment = claim_invoice(invoice_id, reviewer, now_utc(), repo)
    except AlreadyClaimedError as exc:
        # AC-03.1: conflict response naming the current holder.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": str(exc), "held_by": exc.held_by},
        ) from exc
    # claim_invoice() always sets these on a fresh claim; the domain type is nullable
    # because a *released* assignment has neither — cast rather than re-narrow at every
    # call site.
    return ClaimResponse(
        invoice_id=assignment.invoice_id,
        claimed_by=cast(str, assignment.claimed_by),
        expires_at=cast(datetime, assignment.expires_at),
    )


@router.post("/{invoice_id}/release", status_code=status.HTTP_204_NO_CONTENT)
def release(invoice_id: str, reviewer: Reviewer, repo: Repo) -> None:
    try:
        release_invoice(invoice_id, reviewer, now_utc(), repo)
    except NotClaimedByReviewerError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


@router.post("/{invoice_id}/submit", response_model=AssignmentResponse)
def submit(
    invoice_id: str,
    request: SubmitRequest,
    reviewer: Reviewer,
    repo: Repo,
) -> AssignmentResponse:
    if request.outcome == ReviewOutcome.APPROVED:
        # AP forwards; only an admin accepting a forwarded invoice sets APPROVED
        # (POST /api/v1/admin/{invoice_id}/accept). See this module's docstring.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="AP can reject or forward an invoice, not approve it directly — "
            "forward it, and an admin will accept or leave it for further review",
        )
    try:
        assignment = submit_invoice(invoice_id, reviewer, request.outcome, now_utc(), repo)
    except NotClaimedByReviewerError as exc:
        # AC-03.3: rejected if the submitter doesn't hold the lock.
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return AssignmentResponse(
        invoice_id=assignment.invoice_id,
        claimed_by=assignment.claimed_by,
        submitted_at=assignment.submitted_at,
        outcome=assignment.outcome,
    )
