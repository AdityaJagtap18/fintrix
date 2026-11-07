"""In-memory counters for the preprocessing/extraction pipeline's real cost drivers —
files checked, pages dropped before billing, Document Intelligence calls made vs. avoided
via the content-hash cache. Same process-lifetime-only convention as
processing.py's `_ProcessingStore`: this is a running total of what *this server process*
has actually done, not a durable audit trail — it resets on restart, same as everything
else in that store. Nothing here is invented; every field is incremented at the exact
point the real event it names happens.

A lock guards every update because `extract_batch` (processing.py) fans concurrent
extractions out across a thread pool — unlike the GIL-atomic single `list.append()` calls
elsewhere in this codebase, a `+=` here is a real read-modify-write that two threads can
race.

Every increment also goes out through the OpenTelemetry metrics API (`_meter` below),
unconditionally — that's safe and cheap with nothing configured (the API package is a
deliberate no-op by default; see the OpenTelemetry SDK/API split). When api/main.py's
`_telemetry_enabled()` wires up `configure_azure_monitor`, these same numbers start
flowing to the real Application Insights resource named by
APPLICATIONINSIGHTS_CONNECTION_STRING — this module doesn't need to know whether that
happened.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, replace

from opentelemetry import metrics as otel_metrics

_meter = otel_metrics.get_meter("invoice_iq.preprocessing")
_files_checked = _meter.create_counter(
    "preprocessing.files_checked", description="PDFs run through POST /check or /check/zip"
)
_blank_pages_dropped = _meter.create_counter(
    "preprocessing.blank_pages_dropped", description="Blank pages removed before billing"
)
_oversized_rejected = _meter.create_counter(
    "preprocessing.oversized_rejected", description="Files exceeding the page-count gate"
)
_di_calls = _meter.create_counter(
    "preprocessing.di_calls",
    description="Document Intelligence extraction outcomes, labeled by method",
)
_di_pages = _meter.create_counter(
    "preprocessing.di_pages",
    description="Pages, labeled by whether they were billed or cache-avoided",
)

# Per-pipeline-run instruments — one record_pipeline_run() call per extract_and_process()
# attempt (api/routers/processing.py), success or failure.
_pipeline_latency = _meter.create_histogram(
    "pipeline.latency_seconds",
    unit="s",
    description="End-to-end extract_and_process latency, cache hit or real DI call alike",
)
_document_pages = _meter.create_counter(
    "pipeline.document_pages", description="Pages per processed document, labeled by model_id"
)
_field_completion_rate = _meter.create_histogram(
    "pipeline.field_completion_rate",
    description="Fraction of expected invoice fields (vendor_name, invoice_number, "
    "invoice_date, currency, total) actually extracted",
)
# A Gauge, per spec — note this loses distributional information a Histogram would keep
# (each .set() just overwrites the last value for export), so it's the right instrument
# for "current confidence right now" but the free/local avg_confidence_score below (and
# Application Insights' own per-request trace data) are better ways to see a trend over
# many documents.
_confidence_gauge = _meter.create_gauge(
    "pipeline.confidence_score", description="This document's overall routing confidence"
)
_hitl_trigger = _meter.create_counter(
    "pipeline.hitl_triggered",
    description="Runs where a human ended up in the loop — i.e. decision != straight_through, "
    "not a fixed confidence threshold (see record_pipeline_run's docstring)",
)
_pipeline_outcome = _meter.create_counter(
    "pipeline.outcome", description="Pipeline runs, labeled by outcome and status class"
)


@dataclass(frozen=True, slots=True)
class MetricsSnapshot:
    files_checked: int
    blank_pages_dropped: int
    oversized_rejected: int
    total_original_bytes: int
    total_optimized_bytes: int
    di_calls_made: int
    di_calls_avoided: int
    di_pages_billed: int
    di_pages_avoided: int
    pipeline_success_count: int
    pipeline_error_count: int
    hitl_triggered_count: int
    # Running sums, not the average itself — averaging incrementally (new_avg = old_avg +
    # (x - old_avg) / n) would work too, but summing and dividing at read time is simpler
    # and just as correct, and keeps every field here a plain, auditable running total.
    pipeline_latency_seconds_total: float
    field_completion_rate_total: float
    confidence_total: float

    @property
    def cache_hit_rate(self) -> float:
        total = self.di_calls_made + self.di_calls_avoided
        return self.di_calls_avoided / total if total else 0.0

    @property
    def pipeline_runs(self) -> int:
        return self.pipeline_success_count + self.pipeline_error_count

    @property
    def avg_pipeline_latency_seconds(self) -> float:
        return (
            self.pipeline_latency_seconds_total / self.pipeline_success_count
            if self.pipeline_success_count
            else 0.0
        )

    @property
    def avg_field_completion_rate(self) -> float:
        return (
            self.field_completion_rate_total / self.pipeline_success_count
            if self.pipeline_success_count
            else 0.0
        )

    @property
    def avg_confidence_score(self) -> float:
        return (
            self.confidence_total / self.pipeline_success_count
            if self.pipeline_success_count
            else 0.0
        )

    @property
    def hitl_trigger_rate(self) -> float:
        return (
            self.hitl_triggered_count / self.pipeline_success_count
            if self.pipeline_success_count
            else 0.0
        )

    @property
    def cost_per_page(self) -> float | None:
        raw = os.environ.get("DOCUMENT_INTELLIGENCE_COST_PER_PAGE")
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    @property
    def estimated_cost_billed(self) -> float | None:
        rate = self.cost_per_page
        return None if rate is None else round(rate * self.di_pages_billed, 4)

    @property
    def estimated_cost_avoided(self) -> float | None:
        rate = self.cost_per_page
        return None if rate is None else round(rate * self.di_pages_avoided, 4)


class MetricsStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snapshot = MetricsSnapshot(
            files_checked=0,
            blank_pages_dropped=0,
            oversized_rejected=0,
            total_original_bytes=0,
            total_optimized_bytes=0,
            di_calls_made=0,
            di_calls_avoided=0,
            di_pages_billed=0,
            di_pages_avoided=0,
            pipeline_success_count=0,
            pipeline_error_count=0,
            hitl_triggered_count=0,
            pipeline_latency_seconds_total=0.0,
            field_completion_rate_total=0.0,
            confidence_total=0.0,
        )

    def record_check(
        self,
        *,
        blank_pages: int,
        exceeds_limit: bool,
        original_bytes: int,
        optimized_bytes: int,
    ) -> None:
        with self._lock:
            self._snapshot = replace(
                self._snapshot,
                files_checked=self._snapshot.files_checked + 1,
                blank_pages_dropped=self._snapshot.blank_pages_dropped + blank_pages,
                oversized_rejected=self._snapshot.oversized_rejected + (1 if exceeds_limit else 0),
                total_original_bytes=self._snapshot.total_original_bytes + original_bytes,
                total_optimized_bytes=self._snapshot.total_optimized_bytes + optimized_bytes,
            )
        _files_checked.add(1)
        _blank_pages_dropped.add(blank_pages)
        if exceeds_limit:
            _oversized_rejected.add(1)

    def record_extraction(self, *, method: str, page_count: int) -> None:
        with self._lock:
            if method == "cache_reuse":
                self._snapshot = replace(
                    self._snapshot,
                    di_calls_avoided=self._snapshot.di_calls_avoided + 1,
                    di_pages_avoided=self._snapshot.di_pages_avoided + page_count,
                )
            elif method == "azure_document_intelligence":
                self._snapshot = replace(
                    self._snapshot,
                    di_calls_made=self._snapshot.di_calls_made + 1,
                    di_pages_billed=self._snapshot.di_pages_billed + page_count,
                )
        if method == "cache_reuse":
            _di_calls.add(1, {"method": "cache_reuse"})
            _di_pages.add(page_count, {"billed": "false"})
        elif method == "azure_document_intelligence":
            _di_calls.add(1, {"method": "azure_document_intelligence"})
            _di_pages.add(page_count, {"billed": "true"})

    def record_pipeline_run(
        self,
        *,
        outcome: str,  # "success" | "error"
        status_code: int,
        latency_seconds: float,
        page_count: int = 0,
        field_completion_rate: float = 0.0,
        overall_confidence: float = 0.0,
        hitl_triggered: bool = False,
        model_id: str | None = None,
    ) -> None:
        """One call per extract_and_process() attempt. hitl_triggered is the pipeline's
        *actual* routing decision (result.decision != "straight_through" — see
        api/routers/processing.py's call site), not a fixed confidence cutoff: this app's
        real thresholds (core.routing.confidence_router.RoutingPolicy — 0.92 to
        auto-approve, 0.70 to require full review) don't line up with a flat 0.85, and a
        hardcoded shadow threshold here would silently disagree with what the app actually
        did on the same document.
        """
        with self._lock:
            if outcome == "success":
                self._snapshot = replace(
                    self._snapshot,
                    pipeline_success_count=self._snapshot.pipeline_success_count + 1,
                    hitl_triggered_count=self._snapshot.hitl_triggered_count
                    + (1 if hitl_triggered else 0),
                    pipeline_latency_seconds_total=self._snapshot.pipeline_latency_seconds_total
                    + latency_seconds,
                    field_completion_rate_total=self._snapshot.field_completion_rate_total
                    + field_completion_rate,
                    confidence_total=self._snapshot.confidence_total + overall_confidence,
                )
            else:
                self._snapshot = replace(
                    self._snapshot, pipeline_error_count=self._snapshot.pipeline_error_count + 1
                )

        status_class = f"{status_code // 100}xx"
        _pipeline_outcome.add(1, {"outcome": outcome, "status_class": status_class})
        if outcome != "success":
            return
        _pipeline_latency.record(latency_seconds, {"model_id": model_id or "unknown"})
        _document_pages.add(page_count, {"model_id": model_id or "unknown"})
        _field_completion_rate.record(field_completion_rate)
        _confidence_gauge.set(overall_confidence, {"model_id": model_id or "unknown"})
        if hitl_triggered:
            _hitl_trigger.add(1)

    def snapshot(self) -> MetricsSnapshot:
        with self._lock:
            return self._snapshot


metrics = MetricsStore()
