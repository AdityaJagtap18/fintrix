"""Preprocessing: check a file (or a zip of files), see what would happen to it before
it's ever billed, then optionally send the *preprocessed* result into the real extraction
pipeline. Unlike the original cut of this router, checking now genuinely depends on
Cosmos DB (Repos) — the cache-reuse check (#4) has to ask whether this content has
already been extracted, and that's a real lookup, not a local-only computation.
"""

from __future__ import annotations

import io
import json
import time
import zipfile
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import api.routers.processing as processing_module
from api.auth import (
    ensure_quota_available,
    get_current_username,
    get_session,
    record_documents_used,
)
from api.observability import MetricsSnapshot, metrics
from api.routers.processing import _BATCH_MAX_WORKERS as _SEND_MAX_WORKERS
from api.routers.processing import _BATCH_RETRY_DELAY_SECONDS as _SEND_RETRY_DELAY_SECONDS
from api.routers.processing import BatchFileResultDto, Extractor, Repos
from core.extraction.cache import decide_cache_reuse
from core.extraction.preprocessing import (
    EncryptedPdfError,
    UnreadablePdfError,
    detect_invoice_boundaries,
    optimize_document,
    preprocess_document,
)
from core.persistence import InvoiceRepository

router = APIRouter(
    prefix="/api/v1/preprocessing", tags=["preprocessing"], dependencies=[Depends(get_session)]
)


class PreprocessReportDto(BaseModel):
    filename: str
    page_count: int
    content_hash: str
    blank_page_numbers: list[int]
    exceeds_page_limit: bool
    # #4: true when this exact content has already been extracted and is still within
    # the cache window — sending it for extraction will skip Document Intelligence
    # entirely and reuse that result (see api/routers/processing.py's
    # extract_and_process).
    would_reuse_cache: bool
    # #5: heuristic invoice-number boundaries — (start_page, end_page), 1-indexed
    # inclusive. A single-invoice file is just one range covering every page.
    suggested_splits: list[tuple[int, int]]
    original_size_bytes: int
    # #2/#3 applied: blank pages dropped, scan-like pages downsampled + grayscaled,
    # already-digital pages left untouched. Equal to original_size_bytes when nothing
    # needed changing.
    optimized_size_bytes: int
    # Set only for a file inside a zip batch that couldn't be read/was encrypted — a lone
    # POST /check with such a file fails loud (422) instead.
    error: str | None = None


def _would_reuse_cache(invoices: InvoiceRepository, content_hash: str) -> bool:
    cached = invoices.get_by_content_hash(content_hash)
    decision = decide_cache_reuse(
        cached,
        cached_at=cached.extracted_at.date() if cached and cached.extracted_at else None,
        today=date.today(),
    )
    return decision.reuse


@dataclass
class _PreprocessedFile:
    filename: str
    optimized_bytes: bytes
    # Hash of the *original* uploaded bytes — what /check reported and what
    # would_reuse_cache was decided against. optimize_document (blank-page drop,
    # DPI/grayscale) changes the bytes, so re-hashing optimized_bytes at send time would
    # produce a different digest than /check ever showed the user, and the cache could
    # never hit for anything that went through this page. Carrying the original hash
    # through keeps one identity for "this content" end to end.
    content_hash: str
    # Real page count of the *original* file, same reasoning — carried through so
    # extract_and_process doesn't have to re-derive it from optimized_bytes (which may
    # have dropped pages) for the DI-calls-avoided/billed metrics.
    page_count: int


class _PreprocessBatchStore:
    """Bridges this router to the real one: the bytes a batch_id points at are what
    POST /batches/{id}/send actually feeds into processing.py's extract_and_process — the
    *optimized* bytes, not whatever was originally uploaded. In-memory, same
    process-lifetime-only spirit as processing.py's own _ProcessingStore; a batch is
    consumed (popped) on send, not kept around for a second send.
    """

    def __init__(self) -> None:
        self._batches: dict[str, list[_PreprocessedFile]] = {}

    def save(self, files: list[_PreprocessedFile]) -> str:
        batch_id = uuid4().hex
        self._batches[batch_id] = files
        return batch_id

    def pop(self, batch_id: str) -> list[_PreprocessedFile]:
        try:
            return self._batches.pop(batch_id)
        except KeyError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"no preprocessed batch {batch_id!r} (already sent, or never existed)",
            ) from None


_batches = _PreprocessBatchStore()


def _build_report(content: bytes, filename: str, repos: Repos) -> tuple[PreprocessReportDto, bytes]:
    """Returns the report plus the optimized bytes to actually send onward."""
    parsed = preprocess_document(content, filename)
    optimized = optimize_document(
        content, filename, drop_pages=frozenset(parsed.blank_page_numbers)
    )
    splits = detect_invoice_boundaries(content, filename)
    report = PreprocessReportDto(
        filename=filename,
        page_count=parsed.page_count,
        content_hash=parsed.content_hash,
        blank_page_numbers=list(parsed.blank_page_numbers),
        exceeds_page_limit=parsed.exceeds_page_limit,
        would_reuse_cache=_would_reuse_cache(repos.invoices, parsed.content_hash),
        suggested_splits=[(r.start_page, r.end_page) for r in splits],
        original_size_bytes=len(content),
        optimized_size_bytes=len(optimized),
    )
    metrics.record_check(
        blank_pages=len(parsed.blank_page_numbers),
        exceeds_limit=parsed.exceeds_page_limit,
        original_bytes=len(content),
        optimized_bytes=len(optimized),
    )
    return report, optimized


def _pages_actually_sent(report: PreprocessReportDto) -> int:
    """Pages billing/metrics should count, not the original file's page count:
    optimize_document's only page-count-changing operation is dropping blank pages, so
    what actually reaches Document Intelligence is the original count minus those. Blank
    pages the user never sees billed shouldn't show up as billed in the metrics either.
    """
    return report.page_count - len(report.blank_page_numbers)


class CheckResponseDto(BaseModel):
    report: PreprocessReportDto
    batch_id: str


@router.post("/check", response_model=CheckResponseDto)
async def check_one(file: Annotated[UploadFile, File()], repos: Repos) -> CheckResponseDto:
    content = await file.read()
    filename = file.filename or "upload.pdf"
    try:
        report, optimized = _build_report(content, filename, repos)
    except (UnreadablePdfError, EncryptedPdfError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    batch_id = _batches.save(
        [
            _PreprocessedFile(
                filename=filename,
                optimized_bytes=optimized,
                content_hash=report.content_hash,
                page_count=_pages_actually_sent(report),
            )
        ]
    )
    return CheckResponseDto(report=report, batch_id=batch_id)


class BatchPreprocessSummaryDto(BaseModel):
    total_files: int
    unreadable: int
    total_pages: int
    total_blank_pages: int
    exceeds_page_limit_count: int
    cache_hit_count: int
    total_original_size_bytes: int
    total_optimized_size_bytes: int


class BatchPreprocessResponseDto(BaseModel):
    summary: BatchPreprocessSummaryDto
    reports: list[PreprocessReportDto]
    # None when every file in the zip was unreadable — nothing sendable, so there's no
    # batch to send.
    batch_id: str | None


def _extract_pdfs_from_zip(content: bytes) -> list[tuple[str, bytes]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"could not read this as a zip archive: {exc}",
        ) from exc
    return [
        (name, archive.read(name))
        for name in archive.namelist()
        if name.lower().endswith(".pdf")
        and not name.startswith("__MACOSX/")
        and not name.endswith("/")
    ]


@router.post("/check/zip", response_model=BatchPreprocessResponseDto)
async def check_zip(
    file: Annotated[UploadFile, File()], repos: Repos
) -> BatchPreprocessResponseDto:
    content = await file.read()
    pdf_files = _extract_pdfs_from_zip(content)

    reports: list[PreprocessReportDto] = []
    to_send: list[_PreprocessedFile] = []
    for name, pdf_bytes in pdf_files:
        try:
            report, optimized = _build_report(pdf_bytes, name, repos)
        except (UnreadablePdfError, EncryptedPdfError) as exc:
            reports.append(
                PreprocessReportDto(
                    filename=name,
                    page_count=0,
                    content_hash="",
                    blank_page_numbers=[],
                    exceeds_page_limit=False,
                    would_reuse_cache=False,
                    suggested_splits=[],
                    original_size_bytes=len(pdf_bytes),
                    optimized_size_bytes=0,
                    error=str(exc),
                )
            )
            continue
        reports.append(report)
        to_send.append(
            _PreprocessedFile(
                filename=name,
                optimized_bytes=optimized,
                content_hash=report.content_hash,
                page_count=_pages_actually_sent(report),
            )
        )

    readable = [r for r in reports if r.error is None]
    summary = BatchPreprocessSummaryDto(
        total_files=len(pdf_files),
        unreadable=len(pdf_files) - len(readable),
        total_pages=sum(r.page_count for r in readable),
        total_blank_pages=sum(len(r.blank_page_numbers) for r in readable),
        exceeds_page_limit_count=sum(1 for r in readable if r.exceeds_page_limit),
        cache_hit_count=sum(1 for r in readable if r.would_reuse_cache),
        total_original_size_bytes=sum(r.original_size_bytes for r in readable),
        total_optimized_size_bytes=sum(r.optimized_size_bytes for r in readable),
    )
    batch_id = _batches.save(to_send) if to_send else None
    return BatchPreprocessResponseDto(summary=summary, reports=reports, batch_id=batch_id)


def _send_one(extractor: Extractor, repos: Repos, f: _PreprocessedFile) -> BatchFileResultDto:
    """Mirrors processing.py's own extract_batch._process_one exactly — same single
    retry-on-throttle, same conversion of a permanent HTTPException into a per-file error
    instead of sinking the whole batch."""
    for attempt in range(2):
        try:
            result = processing_module._store.extract_and_process(
                extractor,
                f.optimized_bytes,
                f.filename,
                repos,
                content_hash=f.content_hash,
                page_count=f.page_count,
            )
            return BatchFileResultDto(filename=f.filename, status="success", result=result)
        except HTTPException as exc:
            if exc.status_code == status.HTTP_503_SERVICE_UNAVAILABLE and attempt == 0:
                time.sleep(_SEND_RETRY_DELAY_SECONDS)
                continue
            return BatchFileResultDto(filename=f.filename, status="error", error=str(exc.detail))
    raise AssertionError("unreachable")  # loop always returns or continues once


@router.post("/batches/{batch_id}/send")
async def send_batch_for_extraction(
    batch_id: str,
    extractor: Extractor,
    repos: Repos,
    username: Annotated[str, Depends(get_current_username)],
) -> StreamingResponse:
    """The bridge to the real pipeline: every file this batch_id points at — already
    blank-pages-dropped and downsampled/grayscaled by /check or /check/zip — goes through
    processing.py's actual extract_and_process, the same one POST /extract uses. Cache
    reuse (#4) is enforced there, not here; a file this report called would_reuse_cache on
    genuinely skips Document Intelligence when sent.

    Streamed as newline-delimited JSON rather than one JSON array, because each file here
    is a real network round-trip to Document Intelligence — for a multi-file batch that's
    genuine, multi-second wall-clock time with nothing to show for it under the old
    single-response shape. One `{"type": "progress", ...}` line lands as each file
    actually finishes (bounded concurrency, same _BATCH_MAX_WORKERS cap as
    POST /extract/batch), so the UI can show real "N of M done" — not a simulated bar —
    followed by one final `{"type": "done", "results": [...]}` line.

    Quota (api/auth.py) is checked against the whole batch size *before* the stream even
    starts — a plain 403, not a partial stream — and only files that actually succeeded
    count against it once every file's done.
    """
    files = _batches.pop(batch_id)
    total = len(files)
    ensure_quota_available(username, needed=total)

    def _stream() -> Iterator[str]:
        completed = 0
        results: list[BatchFileResultDto | None] = [None] * total
        with ThreadPoolExecutor(max_workers=_SEND_MAX_WORKERS) as pool:
            futures = {
                pool.submit(_send_one, extractor, repos, f): index for index, f in enumerate(files)
            }
            for future in as_completed(futures):
                index = futures[future]
                outcome = future.result()
                results[index] = outcome
                completed += 1
                yield (
                    json.dumps(
                        {
                            "type": "progress",
                            "completed": completed,
                            "total": total,
                            "filename": outcome.filename,
                            "status": outcome.status,
                        }
                    )
                    + "\n"
                )
        record_documents_used(username, sum(1 for r in results if r and r.status == "success"))
        yield (
            json.dumps(
                {
                    "type": "done",
                    "results": [r.model_dump(mode="json") for r in results if r is not None],
                }
            )
            + "\n"
        )

    return StreamingResponse(_stream(), media_type="application/x-ndjson")


class MetricsDto(BaseModel):
    files_checked: int
    blank_pages_dropped: int
    oversized_rejected: int
    total_original_bytes: int
    total_optimized_bytes: int
    di_calls_made: int
    di_calls_avoided: int
    di_pages_billed: int
    di_pages_avoided: int
    cache_hit_rate: float
    cost_per_page: float | None
    estimated_cost_billed: float | None
    estimated_cost_avoided: float | None
    # Pipeline-level (extract_and_process) telemetry — one run per file sent for real
    # extraction, success or failure.
    pipeline_runs: int
    pipeline_success_count: int
    pipeline_error_count: int
    avg_pipeline_latency_seconds: float
    avg_field_completion_rate: float
    avg_confidence_score: float
    # Real routing outcome (decision != straight_through), not a fixed confidence cutoff
    # — see api/observability.py's record_pipeline_run docstring.
    hitl_triggered_count: int
    hitl_trigger_rate: float


def _metrics_dto(snapshot: MetricsSnapshot) -> MetricsDto:
    return MetricsDto(
        files_checked=snapshot.files_checked,
        blank_pages_dropped=snapshot.blank_pages_dropped,
        oversized_rejected=snapshot.oversized_rejected,
        total_original_bytes=snapshot.total_original_bytes,
        total_optimized_bytes=snapshot.total_optimized_bytes,
        di_calls_made=snapshot.di_calls_made,
        di_calls_avoided=snapshot.di_calls_avoided,
        di_pages_billed=snapshot.di_pages_billed,
        di_pages_avoided=snapshot.di_pages_avoided,
        cache_hit_rate=snapshot.cache_hit_rate,
        cost_per_page=snapshot.cost_per_page,
        estimated_cost_billed=snapshot.estimated_cost_billed,
        estimated_cost_avoided=snapshot.estimated_cost_avoided,
        pipeline_runs=snapshot.pipeline_runs,
        pipeline_success_count=snapshot.pipeline_success_count,
        pipeline_error_count=snapshot.pipeline_error_count,
        avg_pipeline_latency_seconds=snapshot.avg_pipeline_latency_seconds,
        avg_field_completion_rate=snapshot.avg_field_completion_rate,
        avg_confidence_score=snapshot.avg_confidence_score,
        hitl_triggered_count=snapshot.hitl_triggered_count,
        hitl_trigger_rate=snapshot.hitl_trigger_rate,
    )


@router.get("/metrics", response_model=MetricsDto)
async def get_metrics() -> MetricsDto:
    """Real counters only — incremented at the exact point each event happens (see
    api/observability.py). Process-lifetime only: resets on restart, same as every other
    in-memory store this app already keeps (_ProcessingStore, _PreprocessBatchStore).
    cost_per_page (and the two estimates derived from it) are null until
    DOCUMENT_INTELLIGENCE_COST_PER_PAGE is set in the environment — no guessed price ships
    by default.
    """
    return _metrics_dto(metrics.snapshot())
