"""A minimal, engine-agnostic orchestration context contract.

`orchestrator.py`'s two generators are written against this shape instead of the Azure
Durable Functions SDK, so nothing in this project depends on it. Whatever actually drives
these generators to completion — a self-hosted durable-task runner, a Celery/RQ-based
worker, Temporal, or (today) a unit test stepping the generator by hand, see
tests/unit/test_orchestrator.py — just needs to satisfy this interface. It mirrors the
Durable Functions context closely on purpose: the orchestration logic itself doesn't change
no matter which runtime ends up driving it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    first_retry_interval_in_milliseconds: int
    max_number_of_attempts: int


class OrchestrationTask(Protocol):
    def cancel(self) -> None: ...

    @property
    def is_completed(self) -> bool: ...


class OrchestrationContext(Protocol):
    def get_input(self) -> Any: ...

    def call_activity(self, name: str, input_: Any = None) -> OrchestrationTask: ...

    def call_activity_with_retry(
        self, name: str, retry_policy: RetryPolicy, input_: Any = None
    ) -> OrchestrationTask: ...

    def call_sub_orchestrator(self, name: str, input_: Any = None) -> OrchestrationTask: ...

    def task_all(self, tasks: list[OrchestrationTask]) -> OrchestrationTask: ...

    def task_any(self, tasks: list[OrchestrationTask]) -> OrchestrationTask: ...

    def wait_for_external_event(self, name: str) -> OrchestrationTask: ...

    def create_timer(self, fire_at: datetime) -> OrchestrationTask: ...

    @property
    def current_utc_datetime(self) -> datetime: ...
