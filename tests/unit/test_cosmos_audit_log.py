from infrastructure.repositories.audit_log import CosmosAuditLogWriter
from tests.unit.fakes import FakeContainer, FakeDatabase


def _make_writer() -> CosmosAuditLogWriter:
    database = FakeDatabase(
        containers={"audit_log": FakeContainer(partition_key_path="/entity_id")}
    )
    return CosmosAuditLogWriter(database)


def test_record_writes_every_field():
    writer = _make_writer()
    writer.record(
        entity_type="invoice",
        entity_id="inv-1",
        action="correction",
        actor="priya",
        actor_role="reviewer",
        before={"total": "1000.00"},
        after={"total": "1180.00"},
        reason="tax total was missing",
    )

    [item] = writer._audit_log._items.values()
    assert item["entity_type"] == "invoice"
    assert item["entity_id"] == "inv-1"
    assert item["action"] == "correction"
    assert item["actor"] == "priya"
    assert item["actor_role"] == "reviewer"
    assert item["before"] == {"total": "1000.00"}
    assert item["after"] == {"total": "1180.00"}
    assert item["reason"] == "tax total was missing"
    assert item["occurred_at"]  # a timestamp was stamped, don't assert on wall-clock time


def test_record_never_overwrites_a_prior_record_for_the_same_entity():
    writer = _make_writer()
    writer.record(
        entity_type="invoice",
        entity_id="inv-1",
        action="claim",
        actor="priya",
        actor_role="reviewer",
        before=None,
        after=None,
    )
    writer.record(
        entity_type="invoice",
        entity_id="inv-1",
        action="approve",
        actor="priya",
        actor_role="reviewer",
        before=None,
        after=None,
    )

    actions = {item["action"] for item in writer._audit_log._items.values()}
    assert actions == {"claim", "approve"}
