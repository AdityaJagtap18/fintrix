"""The Durable Functions orchestrators (plan.md §4, T-051).

Two orchestrator functions, matching the fan-out point in the pipeline (stage 3, split):

* `document_intake_orchestrator` — stages 1-3 (ingest, classify, split). Fans out one
  `invoice_processing_orchestrator` sub-orchestration per detected invoice.
* `invoice_processing_orchestrator` — stages 4-12 for a single invoice (extract through
  vendor notification).

Both are Python generators that only ever `yield` a call into the Durable Functions
context — no direct I/O, no `datetime.now()` (use `context.current_utc_datetime`), no
randomness. That is the whole of what "replays deterministically" (T-051 done-when) means:
the runtime re-executes this generator from the top on every checkpoint, and on replay it
must ask for exactly the same activities in exactly the same order, or replay corrupts
state instead of reproducing it. Idempotency (Article V) is the activities' job, not this
file's — an activity must tolerate being asked to do the same thing twice, because replay
means it sometimes will be.
"""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Any

import azure.durable_functions as df
from azure.durable_functions.models.RetryOptions import RetryOptions

# AC-11.4: no reviewer action for 48h escalates to the manager — and stays in the queue,
# it does not give up.
ESCALATION_TIMEOUT_HOURS = 48


def _cheap_retry() -> RetryOptions:
    return RetryOptions(first_retry_interval_in_milliseconds=1_000, max_number_of_attempts=5)


def _standard_retry(max_attempts: int) -> RetryOptions:
    return RetryOptions(
        first_retry_interval_in_milliseconds=5_000, max_number_of_attempts=max_attempts
    )


@dataclass(frozen=True, slots=True)
class DocumentIntakeInput:
    document_id: str
    blob_uri: str


def document_intake_orchestrator(
    context: df.DurableOrchestrationContext,
) -> Generator[Any, Any, dict[str, Any]]:
    """Stages 1-3: ingest, classify, split. Terminates early (quarantine) without ever
    reaching extraction if the document isn't a processable invoice."""
    raw_input = context.get_input()
    intake = DocumentIntakeInput(**raw_input)

    # 1. Ingest — hash, dedupe (FR-003), page count, quarantine unopenable (FR-005). No
    # retry: a file that can't be opened won't open on a second try, and dedupe/hashing
    # are local, non-transient operations.
    ingest_result = yield context.call_activity("ingest_document", asdict(intake))
    if ingest_result["quarantined"]:
        return _terminal(intake.document_id, "quarantined", ingest_result["reason"])

    # 2. Classify — quarantine non-invoices (FR-010, AC-11.1).
    classification = yield context.call_activity_with_retry(
        "classify_document", _cheap_retry(), {"document_id": intake.document_id}
    )
    if classification["disposition"] == "quarantine":
        yield context.call_activity(
            "notify_vendor",
            {
                "document_id": intake.document_id,
                "event": "rejected",
                "reason": classification["reason"],
            },
        )
        return _terminal(intake.document_id, "quarantined", classification["reason"])

    # 3. Split — one or more invoices, each traceable to source file and page range
    # (FR-011, AC-07.1).
    splits = yield context.call_activity_with_retry(
        "split_document", _standard_retry(max_attempts=3), {"document_id": intake.document_id}
    )

    # AC-07.2: fan out with task_all, not a serial loop — one segment failing extraction
    # must not stop the others from completing. Durable Functions' fan-out/fan-in pattern
    # already gives us this: each sub-orchestration fails or succeeds independently.
    sub_tasks = [
        context.call_sub_orchestrator("invoice_processing_orchestrator", split) for split in splits
    ]
    results = yield context.task_all(sub_tasks)
    return {"document_id": intake.document_id, "invoices": results}


def invoice_processing_orchestrator(
    context: df.DurableOrchestrationContext,
) -> Generator[Any, Any, dict[str, Any]]:
    """Stages 4-12 for a single invoice: extract through vendor notification."""
    split = context.get_input()
    invoice_id = split["invoice_id"]

    # 4. Extract — billed. Transient-only retry: a PermanentExtractionError (malformed
    # input, a 4xx from the engine) must never be retried (Article XII) — that's an
    # engine-adapter contract, not something the orchestrator enforces itself, but the
    # retry count here is capped low precisely because retries cost money.
    extraction = yield context.call_activity_with_retry(
        "extract_invoice", _standard_retry(max_attempts=3), split
    )

    # 5. Normalise — pure, in-process (FR-025). Nothing here can fail transiently.
    normalised = yield context.call_activity("normalise_invoice", extraction)

    # 6. Resolve vendor — tax ID exact, then fuzzy name >= 88 (BR-11, BR-12).
    vendor_resolution = yield context.call_activity_with_retry(
        "resolve_vendor", _standard_retry(max_attempts=5), normalised
    )

    # 7a/7b. Validate and match run in parallel (plan.md §4: "parallel with 7a/7b") — they
    # read the same inputs independently and don't depend on each other's output.
    validate_task = context.call_activity(
        "validate_invoice", {"invoice": normalised, "vendor_resolution": vendor_resolution}
    )
    match_task = context.call_activity_with_retry(
        "three_way_match", _standard_retry(max_attempts=5), normalised
    )
    validation, match = yield context.task_all([validate_task, match_task])

    # 8. Route — pure (core.routing.confidence_router.route). No retry: nothing here does I/O.
    routing = yield context.call_activity(
        "route_invoice",
        {"invoice": normalised, "issues": validation, "match": match, "vendor": vendor_resolution},
    )

    # 9. Persist — idempotent on document_id (Article V): a replay must not create a
    # second row.
    yield context.call_activity_with_retry(
        "persist_result",
        _standard_retry(max_attempts=5),
        {"invoice_id": invoice_id, "routing": routing, "validation": validation, "match": match},
    )

    if routing["decision"] == "straight_through":
        # 10a. Hand off — STP path only. Idempotency key = invoice_id (FR-081): a replayed
        # hand-off after a checkpoint must not emit a second payment instruction. This
        # activity only ever produces an instruction — it cannot execute a transfer
        # (FR-084); that boundary is enforced in core, not here.
        yield context.call_activity_with_retry(
            "emit_payment_instruction", _standard_retry(max_attempts=10), {"invoice_id": invoice_id}
        )
    else:
        # 10b. Enqueue for review, then wait for a human — racing a 48h escalation timer
        # (AC-11.4).
        yield context.call_activity_with_retry(
            "enqueue_for_review", _standard_retry(max_attempts=5), {"invoice_id": invoice_id}
        )
        yield from _wait_for_review_with_escalation(context, invoice_id)

    # 11. Record corrections / shadow agreement (FR-070, FR-058).
    yield context.call_activity_with_retry(
        "record_corrections", _standard_retry(max_attempts=5), {"invoice_id": invoice_id}
    )

    # 12. Notify vendor — reference and status only, never amounts or bank details (FR-001b).
    yield context.call_activity_with_retry(
        "notify_vendor",
        _standard_retry(max_attempts=5),
        {"document_id": split["document_id"], "invoice_id": invoice_id, "event": "terminal"},
    )

    return {"invoice_id": invoice_id, "decision": routing["decision"]}


def _wait_for_review_with_escalation(
    context: df.DurableOrchestrationContext, invoice_id: str
) -> Generator[Any, Any, None]:
    """AC-11.4: escalate once after 48h with no reviewer action, then keep waiting for the
    real `ReviewCompleted` event — escalation is a side effect, not a way out of the loop.
    """
    already_escalated = False
    while True:
        deadline = context.current_utc_datetime + timedelta(hours=ESCALATION_TIMEOUT_HOURS)
        timer_task = context.create_timer(deadline)
        event_task = context.wait_for_external_event("ReviewCompleted")
        winner = yield context.task_any([timer_task, event_task])
        if winner == event_task:
            if not timer_task.is_completed:
                timer_task.cancel()
            return
        if not already_escalated:
            yield context.call_activity("escalate_review", {"invoice_id": invoice_id})
            already_escalated = True
        # Still waiting for the same ReviewCompleted event — the invoice stays queued.


def _terminal(document_id: str, state: str, reason: str) -> dict[str, Any]:
    return {"document_id": document_id, "state": state, "reason": reason}


# Wiring these two generators up as actual Azure Functions triggers — `function_app.py`
# with `@app.orchestration_trigger`, `host.json`, the activity function bindings — is
# integration work for T-052/T-049, once the activities they call (extract_invoice,
# persist_result, etc.) have real implementations behind them. This module is deliberately
# just the orchestration logic, so it can be unit tested (see
# tests/unit/test_orchestrator.py) without a Functions host at all.
