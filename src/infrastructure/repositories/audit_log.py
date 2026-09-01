"""Cosmos DB implementation of `core.persistence.AuditLogWriter`, backed by the `audit_log`
container. `record` only ever calls `create_item` — never `replace_item`/`upsert_item` — so
this code path matches the restricted `audit-log-writer` RBAC role in
`infra/modules/cosmos.bicep` even before Cosmos itself would reject anything else
(Article VIII / T-046's done-when).
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from azure.cosmos.database import DatabaseProxy

from infrastructure.repositories.serialization import datetime_to_item, now_utc


class CosmosAuditLogWriter:
    """Satisfies `AuditLogWriter` structurally (Article I)."""

    def __init__(self, database: DatabaseProxy) -> None:
        self._audit_log = database.get_container_client("audit_log")

    def record(
        self,
        *,
        entity_type: str,
        entity_id: str,
        action: str,
        actor: str,
        actor_role: str,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        reason: str | None = None,
    ) -> None:
        self._audit_log.create_item(
            {
                "id": str(uuid4()),
                "entity_type": entity_type,
                "entity_id": entity_id,
                "action": action,
                "actor": actor,
                "actor_role": actor_role,
                "before": before,
                "after": after,
                "reason": reason,
                "occurred_at": datetime_to_item(now_utc()),
            }
        )
