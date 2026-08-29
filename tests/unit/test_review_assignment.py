from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pytest

from core.models import ReviewAssignment, ReviewOutcome
from core.review.assignment import (
    AlreadyClaimedError,
    NotClaimedByReviewerError,
    claim,
    release,
    submit,
    sweep_expired_locks,
)

NOW = datetime(2026, 8, 29, 10, 0, 0)


@dataclass
class FakeReviewAssignmentRepository:
    store: dict[str, ReviewAssignment] = field(default_factory=dict)

    def get(self, invoice_id: str) -> ReviewAssignment | None:
        return self.store.get(invoice_id)

    def save(self, assignment: ReviewAssignment) -> None:
        self.store[assignment.invoice_id] = assignment

    def find_expired(self, now: datetime) -> list[ReviewAssignment]:
        return [a for a in self.store.values() if a.is_expired(now)]


def test_claim_succeeds_on_an_unclaimed_invoice():
    repo = FakeReviewAssignmentRepository()
    assignment = claim("inv-1", "priya", NOW, repo)
    assert assignment.claimed_by == "priya"
    assert assignment.expires_at == NOW + timedelta(minutes=30)
    assert repo.get("inv-1") is assignment


def test_ac_03_1_second_claim_rejected_naming_current_holder():
    repo = FakeReviewAssignmentRepository()
    claim("inv-1", "priya", NOW, repo)
    with pytest.raises(AlreadyClaimedError) as exc_info:
        claim("inv-1", "rajesh", NOW, repo)
    assert exc_info.value.held_by == "priya"
    assert exc_info.value.invoice_id == "inv-1"


def test_reclaiming_your_own_active_lock_is_a_no_op_success():
    # Not a conflict: a duplicate claim call from the same reviewer against their own
    # still-active lock (e.g. React StrictMode double-firing a mount effect, or a client
    # retry) must not raise AlreadyClaimedError against yourself.
    repo = FakeReviewAssignmentRepository()
    first = claim("inv-1", "priya", NOW, repo)
    second = claim("inv-1", "priya", NOW, repo)
    assert second.claimed_by == "priya"
    assert second.expires_at == first.expires_at


def test_claim_on_an_expired_lock_succeeds_immediately_without_waiting_for_sweep():
    repo = FakeReviewAssignmentRepository()
    claim("inv-1", "priya", NOW, repo, lock_duration=timedelta(minutes=30))
    later = NOW + timedelta(minutes=31)
    assignment = claim("inv-1", "rajesh", later, repo)
    assert assignment.claimed_by == "rajesh"


def test_claim_after_release_succeeds():
    repo = FakeReviewAssignmentRepository()
    claim("inv-1", "priya", NOW, repo)
    release("inv-1", "priya", NOW, repo)
    assignment = claim("inv-1", "rajesh", NOW, repo)
    assert assignment.claimed_by == "rajesh"


def test_release_by_non_holder_is_rejected():
    repo = FakeReviewAssignmentRepository()
    claim("inv-1", "priya", NOW, repo)
    with pytest.raises(NotClaimedByReviewerError):
        release("inv-1", "rajesh", NOW, repo)


def test_release_on_unclaimed_invoice_is_rejected():
    repo = FakeReviewAssignmentRepository()
    with pytest.raises(NotClaimedByReviewerError):
        release("inv-1", "priya", NOW, repo)


def test_submit_by_holder_succeeds_and_records_outcome():
    repo = FakeReviewAssignmentRepository()
    claim("inv-1", "priya", NOW, repo)
    submitted = submit("inv-1", "priya", ReviewOutcome.APPROVED, NOW, repo)
    assert submitted.submitted_at == NOW
    assert submitted.outcome == ReviewOutcome.APPROVED
    assert submitted.is_claimed is False  # submitted_at set -> no longer an active claim


def test_ac_03_3_submit_without_the_lock_is_rejected():
    repo = FakeReviewAssignmentRepository()
    with pytest.raises(NotClaimedByReviewerError):
        submit("inv-1", "priya", ReviewOutcome.APPROVED, NOW, repo)


def test_submit_by_wrong_reviewer_is_rejected():
    repo = FakeReviewAssignmentRepository()
    claim("inv-1", "priya", NOW, repo)
    with pytest.raises(NotClaimedByReviewerError):
        submit("inv-1", "rajesh", ReviewOutcome.APPROVED, NOW, repo)


def test_submit_on_expired_claim_is_rejected():
    repo = FakeReviewAssignmentRepository()
    claim("inv-1", "priya", NOW, repo, lock_duration=timedelta(minutes=30))
    later = NOW + timedelta(minutes=31)
    with pytest.raises(NotClaimedByReviewerError):
        submit("inv-1", "priya", ReviewOutcome.APPROVED, later, repo)


def test_double_submit_is_rejected():
    repo = FakeReviewAssignmentRepository()
    claim("inv-1", "priya", NOW, repo)
    submit("inv-1", "priya", ReviewOutcome.APPROVED, NOW, repo)
    with pytest.raises(NotClaimedByReviewerError):
        submit("inv-1", "priya", ReviewOutcome.REJECTED, NOW, repo)


# --- AC-03.2: lock expiry sweep ---
def test_ac_03_2_sweep_returns_expired_claims_to_the_queue():
    repo = FakeReviewAssignmentRepository()
    claim("inv-1", "priya", NOW, repo, lock_duration=timedelta(minutes=30))
    claim("inv-2", "rajesh", NOW, repo, lock_duration=timedelta(minutes=30))
    later = NOW + timedelta(minutes=31)

    released = sweep_expired_locks(later, repo)

    assert set(released) == {"inv-1", "inv-2"}
    assert repo.get("inv-1").claimed_by is None
    assert repo.get("inv-2").claimed_by is None
    # and the invoice can now be claimed by someone else
    claim("inv-1", "anita", later, repo)


def test_sweep_leaves_active_and_submitted_claims_alone():
    repo = FakeReviewAssignmentRepository()
    claim("inv-1", "priya", NOW, repo, lock_duration=timedelta(minutes=30))  # still active
    claim("inv-2", "rajesh", NOW, repo, lock_duration=timedelta(minutes=30))
    submit("inv-2", "rajesh", ReviewOutcome.APPROVED, NOW, repo)  # already submitted

    released = sweep_expired_locks(NOW + timedelta(minutes=5), repo)

    assert released == []
    assert repo.get("inv-1").claimed_by == "priya"
    assert repo.get("inv-2").outcome == ReviewOutcome.APPROVED


def test_sweep_with_nothing_expired_returns_empty_list():
    repo = FakeReviewAssignmentRepository()
    assert sweep_expired_locks(NOW, repo) == []
