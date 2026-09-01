from datetime import datetime, timedelta

from core.models import ReviewAssignment, ReviewOutcome
from infrastructure.repositories.review_assignments import CosmosReviewAssignmentRepository
from tests.unit.fakes import FakeContainer, FakeDatabase

NOW = datetime(2026, 8, 29, 10, 0, 0)


def _make_repo() -> CosmosReviewAssignmentRepository:
    database = FakeDatabase(
        containers={"review_assignments": FakeContainer(partition_key_path="/invoice_id")}
    )
    return CosmosReviewAssignmentRepository(database)


def test_save_then_get_round_trips():
    repo = _make_repo()
    assignment = ReviewAssignment(
        invoice_id="inv-1",
        claimed_by="priya",
        claimed_at=NOW,
        expires_at=NOW + timedelta(minutes=30),
    )
    repo.save(assignment)

    assert repo.get("inv-1") == assignment


def test_get_missing_returns_none():
    assert _make_repo().get("inv-does-not-exist") is None


def test_save_round_trips_a_submitted_outcome():
    repo = _make_repo()
    assignment = ReviewAssignment(
        invoice_id="inv-1",
        claimed_by="priya",
        claimed_at=NOW,
        submitted_at=NOW + timedelta(minutes=5),
        outcome=ReviewOutcome.APPROVED,
    )
    repo.save(assignment)

    assert repo.get("inv-1") == assignment


def test_find_expired_only_returns_claimed_unsubmitted_and_past_expiry():
    repo = _make_repo()
    expired = ReviewAssignment(
        invoice_id="inv-expired",
        claimed_by="priya",
        claimed_at=NOW - timedelta(hours=1),
        expires_at=NOW - timedelta(minutes=1),
    )
    not_yet_expired = ReviewAssignment(
        invoice_id="inv-active",
        claimed_by="rajesh",
        claimed_at=NOW,
        expires_at=NOW + timedelta(minutes=30),
    )
    already_submitted = ReviewAssignment(
        invoice_id="inv-submitted",
        claimed_by="priya",
        claimed_at=NOW - timedelta(hours=2),
        expires_at=NOW - timedelta(hours=1),
        submitted_at=NOW - timedelta(minutes=90),
        outcome=ReviewOutcome.APPROVED,
    )
    unclaimed = ReviewAssignment(invoice_id="inv-unclaimed")
    for assignment in (expired, not_yet_expired, already_submitted, unclaimed):
        repo.save(assignment)

    result = repo.find_expired(NOW)

    assert [a.invoice_id for a in result] == ["inv-expired"]
