"""Durable orchestrators are plain Python generators — this drives them by hand, exactly
as the Durable Functions runtime does, but with a fake context instead of a real Functions
host. No Azure resource, no Functions runtime, is needed for any of this.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pytest

from functions.orchestrator.orchestrator import (
    document_intake_orchestrator,
    invoice_processing_orchestrator,
)


@dataclass
class FakeTask:
    kind: str  # "activity" | "sub_orchestrator" | "all" | "any" | "event" | "timer"
    name: Any = None
    input_: Any = None
    is_completed: bool = False
    cancelled: bool = False

    def cancel(self) -> None:
        self.cancelled = True


@dataclass
class FakeContext:
    _input: Any
    calls: list[tuple[str, Any, Any]] = field(default_factory=list)
    now: datetime = datetime(2026, 8, 29, 12, 0, 0)

    def get_input(self) -> Any:
        return self._input

    def call_activity(self, name: str, input_: Any = None) -> FakeTask:
        self.calls.append(("activity", name, input_))
        return FakeTask("activity", name, input_)

    def call_activity_with_retry(
        self, name: str, retry_options: Any, input_: Any = None
    ) -> FakeTask:
        self.calls.append(("activity", name, input_))
        return FakeTask("activity", name, input_)

    def call_sub_orchestrator(self, name: str, input_: Any = None) -> FakeTask:
        self.calls.append(("sub_orchestrator", name, input_))
        return FakeTask("sub_orchestrator", name, input_)

    def task_all(self, tasks: list[FakeTask]) -> FakeTask:
        return FakeTask("all", input_=tasks)

    def task_any(self, tasks: list[FakeTask]) -> FakeTask:
        return FakeTask("any", input_=tasks)

    def wait_for_external_event(self, name: str) -> FakeTask:
        self.calls.append(("wait_event", name, None))
        return FakeTask("event", name)

    def create_timer(self, fire_at: datetime) -> FakeTask:
        self.calls.append(("timer", fire_at, None))
        return FakeTask("timer", fire_at)

    @property
    def current_utc_datetime(self) -> datetime:
        return self.now


def drive(gen: Any, decide: Callable[[FakeTask], Any]) -> Any:
    """Steps an orchestrator generator to completion. `decide(task)` inspects whatever was
    just yielded and returns the value the runtime would have sent back in."""
    sent = None
    while True:
        try:
            task = gen.send(sent)
        except StopIteration as stop:
            return stop.value
        sent = decide(task)


def by_activity_name(results: dict[str, Any]) -> Callable[[FakeTask], Any]:
    """The common case: every activity/sub-orchestrator name maps to a fixed canned
    result, and `task_all` fans that out across its sub-tasks in order."""

    def decide(task: FakeTask) -> Any:
        if task.kind in ("activity", "sub_orchestrator"):
            return results[task.name]
        if task.kind == "all":
            return [results[t.name] for t in task.input_]
        raise AssertionError(f"unexpected task kind in this scenario: {task.kind}")

    return decide


# ---------------------------------------------------------------------------------------
# document_intake_orchestrator
# ---------------------------------------------------------------------------------------
def test_document_intake_single_invoice_happy_path():
    ctx = FakeContext(_input={"document_id": "doc-1", "blob_uri": "blob://raw/doc-1.pdf"})
    gen = document_intake_orchestrator(ctx)

    split = {"document_id": "doc-1", "invoice_id": "inv-1"}
    results = by_activity_name(
        {
            "ingest_document": {"quarantined": False},
            "classify_document": {"disposition": "proceed"},
            "split_document": [split],
            "invoice_processing_orchestrator": {
                "invoice_id": "inv-1",
                "decision": "straight_through",
            },
        }
    )
    outcome = drive(gen, results)

    assert outcome == {
        "document_id": "doc-1",
        "invoices": [{"invoice_id": "inv-1", "decision": "straight_through"}],
    }
    sub_orch_calls = [c for c in ctx.calls if c[0] == "sub_orchestrator"]
    assert len(sub_orch_calls) == 1
    assert sub_orch_calls[0][2] == split


def test_document_intake_multi_invoice_fans_out_to_one_sub_orchestration_per_split():
    ctx = FakeContext(_input={"document_id": "batch-1", "blob_uri": "blob://raw/batch-1.pdf"})
    gen = document_intake_orchestrator(ctx)

    splits = [
        {"document_id": "batch-1", "invoice_id": "inv-1"},
        {"document_id": "batch-1", "invoice_id": "inv-2"},
        {"document_id": "batch-1", "invoice_id": "inv-3"},
    ]
    results = by_activity_name(
        {
            "ingest_document": {"quarantined": False},
            "classify_document": {"disposition": "proceed"},
            "split_document": splits,
            "invoice_processing_orchestrator": {"invoice_id": "unused", "decision": "review"},
        }
    )
    outcome = drive(gen, results)

    sub_orch_calls = [c for c in ctx.calls if c[0] == "sub_orchestrator"]
    assert len(sub_orch_calls) == 3
    assert [c[2] for c in sub_orch_calls] == splits
    assert len(outcome["invoices"]) == 3


def test_document_intake_quarantined_at_ingest_stops_before_classification():
    ctx = FakeContext(_input={"document_id": "doc-1", "blob_uri": "blob://raw/doc-1.pdf"})
    gen = document_intake_orchestrator(ctx)

    results = by_activity_name({"ingest_document": {"quarantined": True, "reason": "corrupt_file"}})
    outcome = drive(gen, results)

    assert outcome == {"document_id": "doc-1", "state": "quarantined", "reason": "corrupt_file"}
    assert [c[1] for c in ctx.calls] == ["ingest_document"]  # AC-11.2: no further stages run


def test_document_intake_quarantined_at_classification_notifies_and_stops():
    # AC-11.1: quarantined with the detected type as reason; sender is notified.
    ctx = FakeContext(_input={"document_id": "doc-1", "blob_uri": "blob://raw/doc-1.pdf"})
    gen = document_intake_orchestrator(ctx)

    results = by_activity_name(
        {
            "ingest_document": {"quarantined": False},
            "classify_document": {"disposition": "quarantine", "reason": "detected_type:other"},
            "notify_vendor": {"sent": True},
        }
    )
    outcome = drive(gen, results)

    assert outcome == {
        "document_id": "doc-1",
        "state": "quarantined",
        "reason": "detected_type:other",
    }
    assert [c[1] for c in ctx.calls] == ["ingest_document", "classify_document", "notify_vendor"]
    assert "split_document" not in [c[1] for c in ctx.calls]


# ---------------------------------------------------------------------------------------
# invoice_processing_orchestrator
# ---------------------------------------------------------------------------------------
def _pipeline_results(decision: str) -> dict[str, Any]:
    return {
        "extract_invoice": {"invoice": {"total": 100}},
        "normalise_invoice": {"total": 100},
        "resolve_vendor": {"vendor_id": "v-1"},
        "validate_invoice": [{"rule_id": "BR-01", "passed": True}],
        "three_way_match": {"status": "matched"},
        "route_invoice": {"decision": decision},
        "persist_result": {"ok": True},
        "emit_payment_instruction": {"ok": True},
        "enqueue_for_review": {"ok": True},
        "record_corrections": {"ok": True},
        "notify_vendor": {"ok": True},
    }


def test_invoice_processing_straight_through_hands_off_and_never_enqueues_for_review():
    ctx = FakeContext(_input={"document_id": "doc-1", "invoice_id": "inv-1"})
    gen = invoice_processing_orchestrator(ctx)

    outcome = drive(gen, by_activity_name(_pipeline_results("straight_through")))

    assert outcome == {"invoice_id": "inv-1", "decision": "straight_through"}
    names = [c[1] for c in ctx.calls]
    assert "emit_payment_instruction" in names
    assert "enqueue_for_review" not in names
    assert names[-1] == "notify_vendor"  # stage 12 always runs last


def test_invoice_processing_review_enqueues_and_waits_for_review_completed_immediately():
    ctx = FakeContext(_input={"document_id": "doc-1", "invoice_id": "inv-1"})
    gen = invoice_processing_orchestrator(ctx)

    def decide(task: FakeTask) -> Any:
        if task.kind == "any":
            timer_task, event_task = task.input_
            return event_task  # the reviewer acts before the 48h timer ever fires
        return by_activity_name(_pipeline_results("review"))(task)

    outcome = drive(gen, decide)

    assert outcome == {"invoice_id": "inv-1", "decision": "review"}
    names = [c[1] for c in ctx.calls]
    assert "enqueue_for_review" in names
    assert "emit_payment_instruction" not in names
    assert "escalate_review" not in names  # no escalation — the event won immediately


def test_invoice_processing_escalates_once_then_completes_when_review_finally_lands():
    # AC-11.4: escalates after 48h with no action, but stays in the queue rather than
    # giving up — the second lap around the wait loop must still succeed normally.
    ctx = FakeContext(_input={"document_id": "doc-1", "invoice_id": "inv-1"})
    gen = invoice_processing_orchestrator(ctx)

    race_outcomes = iter(["timer", "event"])  # first lap: timer wins; second lap: event wins

    def decide(task: FakeTask) -> Any:
        if task.kind == "any":
            timer_task, event_task = task.input_
            return timer_task if next(race_outcomes) == "timer" else event_task
        return by_activity_name({**_pipeline_results("review"), "escalate_review": {"ok": True}})(
            task
        )

    outcome = drive(gen, decide)

    assert outcome == {"invoice_id": "inv-1", "decision": "review"}
    names = [c[1] for c in ctx.calls]
    assert names.count("escalate_review") == 1
    # two timer/event pairs were set up (one per lap of the wait loop)
    assert names.count("ReviewCompleted") == 2


def test_validate_and_match_are_dispatched_before_being_awaited_together():
    # 7a/7b run in parallel: both activity calls must be recorded before the task_all that
    # bundles them resolves — proves they aren't serialised.
    ctx = FakeContext(_input={"document_id": "doc-1", "invoice_id": "inv-1"})
    gen = invoice_processing_orchestrator(ctx)

    seen_both_before_route = {"value": False}

    def decide(task: FakeTask) -> Any:
        if task.kind == "all":
            names_recorded = [c[1] for c in ctx.calls]
            seen_both_before_route["value"] = (
                "validate_invoice" in names_recorded and "three_way_match" in names_recorded
            )
        return by_activity_name(_pipeline_results("straight_through"))(task)

    drive(gen, decide)
    assert seen_both_before_route["value"] is True


@pytest.mark.parametrize("bad_kind", ["nonsense"])
def test_drive_helper_raises_on_unhandled_task_kind(bad_kind):
    # Sanity check on the test harness itself: an unrecognised task shape must fail loudly,
    # not silently resolve to None and mask a real orchestrator bug.
    with pytest.raises(AssertionError):
        by_activity_name({})(FakeTask(bad_kind))
