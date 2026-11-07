"""HTTP-level tests for the preprocessing endpoints. Unlike the original cut of this
router, checking now genuinely depends on Cosmos (the cache-hit check), so these tests
override the same five dependencies test_processing_api.py does — the two routers share
the real extraction pipeline (send_batch_for_extraction calls straight into
processing.py's own _store), so they need the same fakes to stay hermetic.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pymupdf
import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from api.auth import SessionStore, hash_password
from api.dependencies import (
    InMemoryReviewAssignmentRepository,
    get_audit_log_writer,
    get_document_intelligence_extractor,
    get_duplicate_lookup,
    get_invoice_admin,
    get_invoice_repo,
    get_review_repo,
    get_vendor_repo,
)
from api.main import app
from api.observability import MetricsStore
from core.extraction.base import ExtractionRequest, ExtractionResult, PermanentExtractionError
from core.models import ExtractedField, Invoice
from tests.unit.test_processing_api import FakeAuditLog, FakeInvoiceRepo, FakeVendorRepo

_CLEAN_INVOICE_TEXT = "Invoice INV-1001\nBill To: Acme Supplies\nTotal: 1180.00"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    import api.auth as auth_module
    import api.observability as observability_module
    import api.routers.processing as processing_module

    original_store = processing_module._store
    processing_module._store = processing_module._ProcessingStore()
    original_sessions = auth_module._sessions
    auth_module._sessions = SessionStore()
    original_metrics = observability_module.metrics
    observability_module.metrics = MetricsStore()
    processing_module.metrics = observability_module.metrics
    import api.routers.preprocessing as preprocessing_module

    preprocessing_module.metrics = observability_module.metrics

    invoice_repo = FakeInvoiceRepo()
    overrides: dict[Any, Any] = {
        get_vendor_repo: FakeVendorRepo(),
        get_duplicate_lookup: invoice_repo,
        get_invoice_repo: invoice_repo,
        get_invoice_admin: invoice_repo,
        get_audit_log_writer: FakeAuditLog(),
        get_review_repo: InMemoryReviewAssignmentRepository(),
    }

    def _make_override(fake: object) -> Any:
        def _override() -> object:
            return fake

        return _override

    for dependency, fake in overrides.items():
        app.dependency_overrides[dependency] = _make_override(fake)

    # This router now requires a session on every endpoint (dependencies=[Depends(
    # get_session)] in api/routers/preprocessing.py) — authenticate once here and set it
    # as a default header, same reasoning as test_processing_api.py's client fixture.
    credentials_file = tmp_path / "credentials.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(("username", "role", "password_hash"))
    sheet.append(("ap", "ap", hash_password("test-pw")))
    workbook.save(credentials_file)
    monkeypatch.setenv("CREDENTIALS_XLSX_PATH", str(credentials_file))

    with TestClient(app) as test_client:
        login = test_client.post(
            "/api/v1/auth/login", json={"username": "ap", "password": "test-pw"}
        )
        test_client.headers["Authorization"] = f"Bearer {login.json()['token']}"
        yield test_client

    app.dependency_overrides.pop(get_document_intelligence_extractor, None)
    for dependency in overrides:
        app.dependency_overrides.pop(dependency, None)
    processing_module._store = original_store
    auth_module._sessions = original_sessions
    observability_module.metrics = original_metrics
    processing_module.metrics = original_metrics
    preprocessing_module.metrics = original_metrics


def _pdf_bytes(*page_texts: str | None) -> bytes:
    doc = pymupdf.open()
    for text in page_texts:
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), text)
    return doc.tobytes()


def _read_ndjson(resp: Any) -> list[dict[str, Any]]:
    return [json.loads(line) for line in resp.text.strip().split("\n") if line]


def _zip_of(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buf.getvalue()


def test_check_requires_a_session(client: TestClient) -> None:
    # This whole router requires a session now (dependencies=[Depends(get_session)]) —
    # empty string overrides the fixture's default Authorization header for this one
    # call, simulating no session at all.
    resp = client.post(
        "/api/v1/preprocessing/check",
        files={"file": ("clean.pdf", _pdf_bytes(_CLEAN_INVOICE_TEXT), "application/pdf")},
        headers={"Authorization": ""},
    )
    assert resp.status_code == 401


def test_check_one_clean_invoice(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/preprocessing/check",
        files={"file": ("clean.pdf", _pdf_bytes(_CLEAN_INVOICE_TEXT), "application/pdf")},
    )

    assert resp.status_code == 200
    body = resp.json()
    report = body["report"]
    assert report["filename"] == "clean.pdf"
    assert report["blank_page_numbers"] == []
    assert not report["exceeds_page_limit"]
    assert not report["would_reuse_cache"]  # nothing extracted yet
    assert body["batch_id"]


def test_check_one_flags_a_blank_page(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/preprocessing/check",
        files={
            "file": (
                "with-blank.pdf",
                _pdf_bytes(_CLEAN_INVOICE_TEXT, None),
                "application/pdf",
            )
        },
    )

    assert resp.json()["report"]["blank_page_numbers"] == [2]


def test_check_one_unreadable_file_is_a_422(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/preprocessing/check",
        files={"file": ("garbage.pdf", b"not a pdf at all", "application/pdf")},
    )

    assert resp.status_code == 422
    assert "could not read" in resp.json()["detail"]


def test_check_one_encrypted_file_is_a_422(client: TestClient) -> None:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), _CLEAN_INVOICE_TEXT)
    encrypted = doc.tobytes(
        encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="user"
    )

    resp = client.post(
        "/api/v1/preprocessing/check",
        files={"file": ("locked.pdf", encrypted, "application/pdf")},
    )

    assert resp.status_code == 422
    assert "password-protected" in resp.json()["detail"]


def test_check_one_reports_a_cache_hit_for_already_extracted_content(
    client: TestClient,
) -> None:
    content = _pdf_bytes(_CLEAN_INVOICE_TEXT)
    # Seed the fake invoice repo (the same instance the app resolves via the override)
    # with an invoice carrying this exact content's hash, already extracted today —
    # decide_cache_reuse should treat that as fresh.
    seeded = Invoice(
        invoice_id="inv-seed",
        document_id="doc-seed",
        fields={"invoice_number": ExtractedField(name="invoice_number", value="INV-1001")},
        content_hash=hashlib.sha256(content).hexdigest(),
        extracted_at=datetime.now(UTC).replace(tzinfo=None),
    )
    seeded.vendor_id = "v-1"
    invoice_repo = app.dependency_overrides[get_invoice_repo]()
    invoice_repo.save(seeded)

    resp = client.post(
        "/api/v1/preprocessing/check",
        files={"file": ("clean.pdf", content, "application/pdf")},
    )

    assert resp.json()["report"]["would_reuse_cache"] is True


def test_check_zip_summarizes_a_mixed_batch(client: TestClient) -> None:
    zip_bytes = _zip_of(
        {
            "clean.pdf": _pdf_bytes(_CLEAN_INVOICE_TEXT),
            "blank-page.pdf": _pdf_bytes(_CLEAN_INVOICE_TEXT, None),
        }
    )

    resp = client.post(
        "/api/v1/preprocessing/check/zip",
        files={"file": ("batch.zip", zip_bytes, "application/zip")},
    )

    assert resp.status_code == 200
    body = resp.json()
    summary = body["summary"]
    assert summary["total_files"] == 2
    assert summary["unreadable"] == 0
    assert summary["total_blank_pages"] == 1
    assert {r["filename"] for r in body["reports"]} == {"clean.pdf", "blank-page.pdf"}
    assert body["batch_id"]


def test_check_zip_reports_an_unreadable_file_without_failing_the_batch(
    client: TestClient,
) -> None:
    zip_bytes = _zip_of(
        {
            "good.pdf": _pdf_bytes(_CLEAN_INVOICE_TEXT),
            "corrupt.pdf": b"this is not a real pdf",
        }
    )

    resp = client.post(
        "/api/v1/preprocessing/check/zip",
        files={"file": ("batch.zip", zip_bytes, "application/zip")},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["total_files"] == 2
    assert body["summary"]["unreadable"] == 1

    by_name = {r["filename"]: r for r in body["reports"]}
    assert by_name["good.pdf"]["error"] is None
    assert by_name["corrupt.pdf"]["error"] is not None


def test_check_zip_ignores_non_pdf_entries(client: TestClient) -> None:
    zip_bytes = _zip_of(
        {
            "invoice.pdf": _pdf_bytes(_CLEAN_INVOICE_TEXT),
            "readme.txt": b"not a pdf, shouldn't be counted",
            "__MACOSX/._invoice.pdf": b"macOS zip metadata junk",
        }
    )

    resp = client.post(
        "/api/v1/preprocessing/check/zip",
        files={"file": ("batch.zip", zip_bytes, "application/zip")},
    )

    assert resp.json()["summary"]["total_files"] == 1


def test_check_zip_on_a_non_zip_file_is_a_422(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/preprocessing/check/zip",
        files={"file": ("not-a-zip.zip", b"definitely not a zip archive", "application/zip")},
    )

    assert resp.status_code == 422
    assert "zip" in resp.json()["detail"].lower()


def test_check_zip_with_no_pdfs_returns_an_empty_summary_and_no_batch(
    client: TestClient,
) -> None:
    zip_bytes = _zip_of({"readme.txt": b"nothing but text files in here"})

    resp = client.post(
        "/api/v1/preprocessing/check/zip",
        files={"file": ("batch.zip", zip_bytes, "application/zip")},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["total_files"] == 0
    assert body["reports"] == []
    assert body["batch_id"] is None


# --- POST /batches/{id}/send: the bridge into the real extraction pipeline ---


class FakeExtractor:
    def __init__(self, outcome: ExtractionResult | Exception) -> None:
        self.outcome = outcome
        self.calls: list[ExtractionRequest] = []

    def extract(self, request: ExtractionRequest) -> ExtractionResult:
        self.calls.append(request)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _extracted_invoice(invoice_number: str) -> Invoice:
    return Invoice(
        invoice_id="inv-x",
        document_id="inv-x",
        fields={
            "vendor_name": ExtractedField(name="vendor_name", value="Zeta Print Co"),
            "invoice_number": ExtractedField(name="invoice_number", value=invoice_number),
            "total": ExtractedField(name="total", value="500.00"),
        },
    )


def test_send_batch_runs_the_checked_file_through_real_extraction(client: TestClient) -> None:
    fake = FakeExtractor(
        ExtractionResult(
            invoice=_extracted_invoice("INV-7001"),
            model_id="prebuilt-invoice",
            model_version="2024-11-30",
            raw_response={},
        )
    )
    app.dependency_overrides[get_document_intelligence_extractor] = lambda: fake

    check_resp = client.post(
        "/api/v1/preprocessing/check",
        files={"file": ("clean.pdf", _pdf_bytes(_CLEAN_INVOICE_TEXT), "application/pdf")},
    )
    batch_id = check_resp.json()["batch_id"]

    send_resp = client.post(f"/api/v1/preprocessing/batches/{batch_id}/send")

    assert send_resp.status_code == 200
    lines = _read_ndjson(send_resp)
    progress = [line for line in lines if line["type"] == "progress"]
    done = next(line for line in lines if line["type"] == "done")
    assert progress == [
        {
            "type": "progress",
            "completed": 1,
            "total": 1,
            "filename": "clean.pdf",
            "status": "success",
        }
    ]
    results = done["results"]
    assert len(results) == 1
    assert results[0]["status"] == "success"
    assert results[0]["result"]["invoice_number"] == "INV-7001"
    assert results[0]["result"]["extraction_method"] == "azure_document_intelligence"
    assert len(fake.calls) == 1  # the real pipeline genuinely ran


def test_a_file_sent_for_extraction_is_a_cache_hit_on_the_next_check(
    client: TestClient,
) -> None:
    """Regression test for a real bug found via live testing: optimize_document changes
    the bytes (blank-page drop, DPI/grayscale), so if extract_and_process re-hashed
    optimized_bytes at send time, the invoice would be saved under a different
    content_hash than /check ever reports — and the cache could never hit for anything
    that went through this page. The batch must carry the *original* content's hash
    through to send so the two line up.
    """
    fake = FakeExtractor(
        ExtractionResult(
            invoice=_extracted_invoice("INV-7003"),
            model_id="prebuilt-invoice",
            model_version="2024-11-30",
            raw_response={},
        )
    )
    app.dependency_overrides[get_document_intelligence_extractor] = lambda: fake

    # A blank page means optimize_document actually changes the bytes (drops it), so
    # optimized_bytes != content and a hash of one won't equal a hash of the other.
    content = _pdf_bytes(_CLEAN_INVOICE_TEXT, None)
    check_resp = client.post(
        "/api/v1/preprocessing/check",
        files={"file": ("clean.pdf", content, "application/pdf")},
    )
    original_hash = check_resp.json()["report"]["content_hash"]
    batch_id = check_resp.json()["batch_id"]

    send_resp = client.post(f"/api/v1/preprocessing/batches/{batch_id}/send")
    assert send_resp.status_code == 200

    invoice_repo = app.dependency_overrides[get_invoice_repo]()
    saved = invoice_repo.get_by_content_hash(original_hash)
    assert saved is not None  # saved under the *original* content's hash, not the

    recheck_resp = client.post(
        "/api/v1/preprocessing/check",
        files={"file": ("clean.pdf", content, "application/pdf")},
    )
    assert recheck_resp.json()["report"]["would_reuse_cache"] is True
    assert len(fake.calls) == 1  # only the first send actually called Document Intelligence


def test_send_batch_with_an_unknown_id_is_a_404(client: TestClient) -> None:
    resp = client.post("/api/v1/preprocessing/batches/does-not-exist/send")

    assert resp.status_code == 404


def test_send_batch_is_refused_when_it_would_exceed_the_signed_in_users_quota(
    client: TestClient,
) -> None:
    from datetime import date

    from api.auth import UserRecord, hash_password, upsert_user

    upsert_user(
        UserRecord(
            username="demo-quota",
            role="ap",
            password_hash=hash_password("demo-pw"),
            doc_quota=0,
            docs_used=0,
            valid_until=None,
            created_at=date.today(),
        )
    )
    login = client.post(
        "/api/v1/auth/login", json={"username": "demo-quota", "password": "demo-pw"}
    )
    headers = {"Authorization": f"Bearer {login.json()['token']}"}

    fake = FakeExtractor(
        ExtractionResult(
            invoice=_extracted_invoice("INV-9101"),
            model_id="prebuilt-invoice",
            model_version="2024-11-30",
            raw_response={},
        )
    )
    app.dependency_overrides[get_document_intelligence_extractor] = lambda: fake

    check_resp = client.post(
        "/api/v1/preprocessing/check",
        files={"file": ("clean.pdf", _pdf_bytes(_CLEAN_INVOICE_TEXT), "application/pdf")},
        headers=headers,
    )
    batch_id = check_resp.json()["batch_id"]

    send_resp = client.post(f"/api/v1/preprocessing/batches/{batch_id}/send", headers=headers)

    assert send_resp.status_code == 403
    assert len(fake.calls) == 0  # nothing was even attempted


def test_send_batch_cannot_be_sent_twice(client: TestClient) -> None:
    fake = FakeExtractor(
        ExtractionResult(
            invoice=_extracted_invoice("INV-7002"),
            model_id="prebuilt-invoice",
            model_version="2024-11-30",
            raw_response={},
        )
    )
    app.dependency_overrides[get_document_intelligence_extractor] = lambda: fake

    check_resp = client.post(
        "/api/v1/preprocessing/check",
        files={"file": ("clean.pdf", _pdf_bytes(_CLEAN_INVOICE_TEXT), "application/pdf")},
    )
    batch_id = check_resp.json()["batch_id"]

    first = client.post(f"/api/v1/preprocessing/batches/{batch_id}/send")
    second = client.post(f"/api/v1/preprocessing/batches/{batch_id}/send")

    assert first.status_code == 200
    assert second.status_code == 404


def test_send_batch_streams_a_real_progress_line_per_file(client: TestClient) -> None:
    fake = FakeExtractor(
        ExtractionResult(
            invoice=_extracted_invoice("INV-8001"),
            model_id="prebuilt-invoice",
            model_version="2024-11-30",
            raw_response={},
        )
    )
    app.dependency_overrides[get_document_intelligence_extractor] = lambda: fake

    zip_bytes = _zip_of(
        {
            "a.pdf": _pdf_bytes("Invoice INV-A\nBill To: A Co\nTotal: 10.00"),
            "b.pdf": _pdf_bytes("Invoice INV-B\nBill To: B Co\nTotal: 20.00"),
        }
    )
    check_resp = client.post(
        "/api/v1/preprocessing/check/zip",
        files={"file": ("batch.zip", zip_bytes, "application/zip")},
    )
    batch_id = check_resp.json()["batch_id"]

    send_resp = client.post(f"/api/v1/preprocessing/batches/{batch_id}/send")
    assert send_resp.status_code == 200

    lines = _read_ndjson(send_resp)
    progress = [line for line in lines if line["type"] == "progress"]
    done = next(line for line in lines if line["type"] == "done")

    # Real per-file completion events, not a simulated bar: one progress line per file,
    # completed count reaches exactly total, every file accounted for exactly once.
    assert len(progress) == 2
    assert {p["completed"] for p in progress} == {1, 2}
    assert all(p["total"] == 2 for p in progress)
    assert {p["filename"] for p in progress} == {"a.pdf", "b.pdf"}
    assert len(done["results"]) == 2
    assert all(r["status"] == "success" for r in done["results"])
    assert len(fake.calls) == 2


def test_send_batch_reports_a_per_file_error_without_stopping_the_stream(
    client: TestClient,
) -> None:
    fake = FakeExtractor(PermanentExtractionError("garbled response from the engine"))
    app.dependency_overrides[get_document_intelligence_extractor] = lambda: fake

    check_resp = client.post(
        "/api/v1/preprocessing/check",
        files={"file": ("bad.pdf", _pdf_bytes(_CLEAN_INVOICE_TEXT), "application/pdf")},
    )
    batch_id = check_resp.json()["batch_id"]

    send_resp = client.post(f"/api/v1/preprocessing/batches/{batch_id}/send")
    assert send_resp.status_code == 200

    lines = _read_ndjson(send_resp)
    progress = [line for line in lines if line["type"] == "progress"]
    done = next(line for line in lines if line["type"] == "done")

    assert progress[0]["status"] == "error"
    assert done["results"][0]["status"] == "error"
    assert "garbled response" in done["results"][0]["error"]


# --- GET /metrics: real counters, not invented numbers ---


def test_metrics_start_at_zero(client: TestClient) -> None:
    resp = client.get("/api/v1/preprocessing/metrics")

    assert resp.status_code == 200
    body = resp.json()
    assert body["files_checked"] == 0
    assert body["di_calls_made"] == 0
    assert body["di_calls_avoided"] == 0
    assert body["cache_hit_rate"] == 0.0
    assert body["cost_per_page"] is None
    assert body["estimated_cost_billed"] is None


def test_metrics_reflect_a_real_check_and_a_real_extraction(client: TestClient) -> None:
    fake = FakeExtractor(
        ExtractionResult(
            invoice=_extracted_invoice("INV-9101"),
            model_id="prebuilt-invoice",
            model_version="2024-11-30",
            raw_response={},
        )
    )
    app.dependency_overrides[get_document_intelligence_extractor] = lambda: fake

    check_resp = client.post(
        "/api/v1/preprocessing/check",
        files={
            "file": (
                "with-blank.pdf",
                _pdf_bytes(_CLEAN_INVOICE_TEXT, None),
                "application/pdf",
            )
        },
    )
    batch_id = check_resp.json()["batch_id"]
    client.post(f"/api/v1/preprocessing/batches/{batch_id}/send")

    body = client.get("/api/v1/preprocessing/metrics").json()
    assert body["files_checked"] == 1
    assert body["blank_pages_dropped"] == 1
    assert body["di_calls_made"] == 1
    assert body["di_calls_avoided"] == 0
    # 2 real pages, but 1 was blank and dropped before sending — billed count should
    # reflect what was actually sent to Document Intelligence, not the original file.
    assert body["di_pages_billed"] == 1
    assert body["cache_hit_rate"] == 0.0


def test_metrics_count_a_cache_hit_as_avoided_not_billed(client: TestClient) -> None:
    fake = FakeExtractor(
        ExtractionResult(
            invoice=_extracted_invoice("INV-9102"),
            model_id="prebuilt-invoice",
            model_version="2024-11-30",
            raw_response={},
        )
    )
    app.dependency_overrides[get_document_intelligence_extractor] = lambda: fake
    content = _pdf_bytes(_CLEAN_INVOICE_TEXT)

    first_check = client.post(
        "/api/v1/preprocessing/check", files={"file": ("clean.pdf", content, "application/pdf")}
    )
    client.post(f"/api/v1/preprocessing/batches/{first_check.json()['batch_id']}/send")

    second_check = client.post(
        "/api/v1/preprocessing/check", files={"file": ("clean.pdf", content, "application/pdf")}
    )
    client.post(f"/api/v1/preprocessing/batches/{second_check.json()['batch_id']}/send")

    body = client.get("/api/v1/preprocessing/metrics").json()
    assert body["di_calls_made"] == 1
    assert body["di_calls_avoided"] == 1
    assert body["cache_hit_rate"] == 0.5


def test_metrics_estimated_cost_is_null_until_a_rate_is_configured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DOCUMENT_INTELLIGENCE_COST_PER_PAGE", raising=False)
    assert client.get("/api/v1/preprocessing/metrics").json()["estimated_cost_billed"] is None

    monkeypatch.setenv("DOCUMENT_INTELLIGENCE_COST_PER_PAGE", "0.5")
    fake = FakeExtractor(
        ExtractionResult(
            invoice=_extracted_invoice("INV-9103"),
            model_id="prebuilt-invoice",
            model_version="2024-11-30",
            raw_response={},
        )
    )
    app.dependency_overrides[get_document_intelligence_extractor] = lambda: fake
    check_resp = client.post(
        "/api/v1/preprocessing/check",
        files={"file": ("clean.pdf", _pdf_bytes(_CLEAN_INVOICE_TEXT), "application/pdf")},
    )
    client.post(f"/api/v1/preprocessing/batches/{check_resp.json()['batch_id']}/send")

    body = client.get("/api/v1/preprocessing/metrics").json()
    assert body["estimated_cost_billed"] == 0.5  # 1 page * 0.5/page


def test_metrics_reflect_a_real_pipeline_run_including_hitl(client: TestClient) -> None:
    # _extracted_invoice's fields carry the default 0.0 confidence, so the router's real
    # 0.92 auto-threshold sends this to review — a genuine, not-fixed-at-0.85 HITL case.
    fake = FakeExtractor(
        ExtractionResult(
            invoice=_extracted_invoice("INV-9104"),
            model_id="prebuilt-invoice",
            model_version="2024-11-30",
            raw_response={},
        )
    )
    app.dependency_overrides[get_document_intelligence_extractor] = lambda: fake

    check_resp = client.post(
        "/api/v1/preprocessing/check",
        files={"file": ("clean.pdf", _pdf_bytes(_CLEAN_INVOICE_TEXT), "application/pdf")},
    )
    client.post(f"/api/v1/preprocessing/batches/{check_resp.json()['batch_id']}/send")

    body = client.get("/api/v1/preprocessing/metrics").json()
    assert body["pipeline_runs"] == 1
    assert body["pipeline_success_count"] == 1
    assert body["pipeline_error_count"] == 0
    assert body["avg_pipeline_latency_seconds"] > 0.0
    # vendor_name, invoice_number, total are set; invoice_date, currency are not — 3/5.
    assert body["avg_field_completion_rate"] == pytest.approx(0.6)
    assert body["avg_confidence_score"] == pytest.approx(0.0)
    assert body["hitl_triggered_count"] == 1
    assert body["hitl_trigger_rate"] == 1.0


def test_metrics_pipeline_error_is_counted_without_touching_success_averages(
    client: TestClient,
) -> None:
    fake = FakeExtractor(PermanentExtractionError("garbled response"))
    app.dependency_overrides[get_document_intelligence_extractor] = lambda: fake

    check_resp = client.post(
        "/api/v1/preprocessing/check",
        files={"file": ("bad.pdf", _pdf_bytes(_CLEAN_INVOICE_TEXT), "application/pdf")},
    )
    client.post(f"/api/v1/preprocessing/batches/{check_resp.json()['batch_id']}/send")

    body = client.get("/api/v1/preprocessing/metrics").json()
    assert body["pipeline_runs"] == 1
    assert body["pipeline_success_count"] == 0
    assert body["pipeline_error_count"] == 1
    assert body["avg_confidence_score"] == 0.0
    assert body["hitl_trigger_rate"] == 0.0
