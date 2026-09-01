"""Cosmos DB implementation of `core.persistence.ReviewAssignmentRepository`, backed by the
`review_assignments` container (partitioned by `invoice_id`, matching its SQL PK exactly).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from azure.cosmos.database import DatabaseProxy
from azure.cosmos.exceptions import CosmosResourceNotFoundError

from core.models import ReviewAssignment, ReviewOutcome
from infrastructure.repositories.serialization import datetime_from_item, datetime_to_item


def _to_item(assignment: ReviewAssignment) -> dict[str, Any]:
    return {
        "id": assignment.invoice_id,
        "invoice_id": assignment.invoice_id,
        "claimed_by": assignment.claimed_by,
        "claimed_at": datetime_to_item(assignment.claimed_at),
        "expires_at": datetime_to_item(assignment.expires_at),
        "submitted_at": datetime_to_item(assignment.submitted_at),
        "outcome": None if assignment.outcome is None else assignment.outcome.value,
    }


def _from_item(item: dict[str, Any]) -> ReviewAssignment:
    outcome = item.get("outcome")
    return ReviewAssignment(
        invoice_id=item["invoice_id"],
        claimed_by=item.get("claimed_by"),
        claimed_at=datetime_from_item(item.get("claimed_at")),
        expires_at=datetime_from_item(item.get("expires_at")),
        submitted_at=datetime_from_item(item.get("submitted_at")),
        outcome=None if outcome is None else ReviewOutcome(outcome),
    )


class CosmosReviewAssignmentRepository:
    """Satisfies `ReviewAssignmentRepository` structurally (Article I)."""

    def __init__(self, database: DatabaseProxy) -> None:
        self._assignments = database.get_container_client("review_assignments")

    def get(self, invoice_id: str) -> ReviewAssignment | None:
        try:
            item = self._assignments.read_item(invoice_id, partition_key=invoice_id)
        except CosmosResourceNotFoundError:
            return None
        return _from_item(item)

    def save(self, assignment: ReviewAssignment) -> None:
        self._assignments.upsert_item(_to_item(assignment))

    def find_expired(self, now: datetime) -> list[ReviewAssignment]:
        # FR-062's lock-expiry sweep. Mirrors ReviewAssignment.is_expired() exactly
        # (claimed_by set, not yet submitted, past its expiry) rather than relying on
        # release() having cleared expires_at back to null. Cross-partition — an
        # infrequent timer job, not a user-facing path (plan.md §3).
        items = self._assignments.query_items(
            query=(
                "SELECT * FROM c WHERE c.claimed_by != null AND c.submitted_at = null "
                "AND c.expires_at != null AND c.expires_at <= @now"
            ),
            parameters=[{"name": "@now", "value": datetime_to_item(now)}],
            enable_cross_partition_query=True,
        )
        return [_from_item(item) for item in items]
