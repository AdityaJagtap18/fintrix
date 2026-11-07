"""In-memory fakes for the `azure-cosmos` surface T-047's repositories use — no live account
or emulator needed. Not a general-purpose emulator: `FakeContainer.query_items` only
understands the small, regular query grammar the repositories actually issue (a chain of
`c.<field> <op> @<param>` clauses joined by `AND`), not arbitrary Cosmos SQL. If a repository
starts issuing a shape this doesn't cover, extend `_evaluate_clause`, don't work around it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from azure.cosmos.exceptions import CosmosResourceExistsError, CosmosResourceNotFoundError

_CLAUSE_RE = re.compile(r"c\.(\w+)\s*(=|!=|<=|>=)\s*(@\w+|true|false|null)")


def _literal(token: str, params: dict[str, Any]) -> Any:
    if token == "true":
        return True
    if token == "false":
        return False
    if token == "null":
        return None
    return params[token]  # parameters dicts are keyed with the leading '@', same as Cosmos


def _evaluate_clause(field_name: str, op: str, expected: Any, item: dict[str, Any]) -> bool:
    actual = item.get(field_name)
    if op == "=":
        return actual == expected
    if op == "!=":
        return actual != expected
    if op == "<=":
        return actual is not None and actual <= expected
    if op == ">=":
        return actual is not None and actual >= expected
    raise ValueError(f"unsupported operator: {op}")  # pragma: no cover — grammar is closed


def _matches(query: str, params: dict[str, Any], item: dict[str, Any]) -> bool:
    clauses = _CLAUSE_RE.findall(query)
    if not clauses:
        raise ValueError(f"FakeContainer can't parse query: {query!r}")
    return all(
        _evaluate_clause(field_name, op, _literal(token, params), item)
        for field_name, op, token in clauses
    )


@dataclass
class FakeContainer:
    """One container's worth of items, keyed by `(partition_key_value, id)`.

    `unique_key_paths` mirrors a container's `uniqueKeyPolicy`: a create/upsert that
    collides with another item *in the same partition* on one of these top-level fields
    raises `CosmosResourceExistsError`, matching real Cosmos DB behaviour for the
    `invoices` (`/invoice_number`) and `vendor_tax_ids` containers.
    """

    partition_key_path: str
    unique_key_paths: tuple[str, ...] = ()
    _items: dict[tuple[Any, str], dict[str, Any]] = field(default_factory=dict)

    def _partition_key(self, body: dict[str, Any]) -> Any:
        return body[self.partition_key_path.lstrip("/")]

    def _check_unique_keys(self, body: dict[str, Any]) -> None:
        pk = self._partition_key(body)
        for (item_pk, item_id), existing in self._items.items():
            if item_pk != pk or item_id == body["id"]:
                continue
            for path in self.unique_key_paths:
                name = path.lstrip("/")
                if name in body and existing.get(name) == body[name]:
                    raise CosmosResourceExistsError(
                        message=f"unique key conflict on {path} within partition {pk!r}"
                    )

    def create_item(self, body: dict[str, Any]) -> dict[str, Any]:
        key = (self._partition_key(body), body["id"])
        if key in self._items:
            raise CosmosResourceExistsError(message=f"item {body['id']!r} already exists")
        self._check_unique_keys(body)
        self._items[key] = dict(body)
        return dict(body)

    def upsert_item(self, body: dict[str, Any]) -> dict[str, Any]:
        self._check_unique_keys(body)
        self._items[(self._partition_key(body), body["id"])] = dict(body)
        return dict(body)

    def read_item(self, item: str, partition_key: Any) -> dict[str, Any]:
        try:
            return dict(self._items[(partition_key, item)])
        except KeyError:
            raise CosmosResourceNotFoundError(message=f"item {item!r} not found") from None

    def delete_item(self, item: str, partition_key: Any) -> None:
        try:
            del self._items[(partition_key, item)]
        except KeyError:
            raise CosmosResourceNotFoundError(message=f"item {item!r} not found") from None

    def query_items(
        self,
        query: str,
        parameters: list[dict[str, Any]] | None = None,
        partition_key: Any = None,
        enable_cross_partition_query: bool = False,
        **_: Any,
    ) -> list[dict[str, Any]]:
        if partition_key is None and not enable_cross_partition_query:
            raise ValueError(
                "query_items needs either partition_key or enable_cross_partition_query=True"
            )
        params = {p["name"]: p["value"] for p in (parameters or [])}
        candidates = (
            [v for (pk, _), v in self._items.items() if pk == partition_key]
            if partition_key is not None
            else list(self._items.values())
        )
        return [dict(item) for item in candidates if _matches(query, params, item)]


@dataclass
class FakeDatabase:
    containers: dict[str, FakeContainer]

    def get_container_client(self, container: str) -> FakeContainer:
        return self.containers[container]


@dataclass
class FakeCosmosClient:
    """Stands in for `azure.cosmos.CosmosClient` — repositories only ever call
    `get_database_client(...).get_container_client(...)`, so that's all this provides."""

    database: FakeDatabase

    def get_database_client(self, database: str) -> FakeDatabase:
        return self.database
