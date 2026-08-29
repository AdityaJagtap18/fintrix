"""HTTP-level tests for the processing-results and invoice-upload endpoints. Same
TestClient, no-live-server pattern as test_review_api.py.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from api.dependencies import get_document_intelligence_extractor
from api.main import app
from api.routers.processing import (
    MockPaymentInstructionAdapter,
    _DuplicateStore,
    _ProcessingStore,
    _VendorRegistry,
)
from api.routers.processing import _store as module_store
from core.extraction.base import (
    ExtractionRequest,
    ExtractionResult,
    PermanentExtractionError,
    TransientExtractionError,
)
from core.models import BoundingRegion, ExtractedField, Invoice


@pytest.fixture
def client() -> Iterator[TestClient]:
    # Swap in a fresh store per test — the module-level singleton would otherwise leak
    # seeded state (including the duplicate tracker) across tests in the same process.
    import api.routers.processing as processing_module

    original = processing_module._store
    processing_module._store = _ProcessingStore()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.pop(get_document_intelligence_extractor, None)
    processing_module._store = original


def valid_upload(**overrides: object) -> dict:
    payload = {
        "vendor_name": "Beta Traders",
        # A GSTIN that passes BR-09's mod-36 checksum — same one used across the codebase
        # (scripts/demo_process_invoice.py, api/routers/processing.py's own demo vendor).
        "vendor_tax_id": "27AAPFU0939F1ZV",
        "invoice_number": "INV-9001",
        "invoice_date": "2026-08-10",
        "due_date": "2026-09-09",
        "currency": "INR",
        "subtotal": "2000.00",
        "tax_total": "360.00",
        "total": "2360.00",
        "amount_due": "2360.00",
        "confidence": 0.97,
        "vendor_requires_po": False,
        "vendor_invoices_processed": 25,
        "vendor_bank_account": "ICIC0009999-111222333",
        "source_filename": "beta-traders-inv-9001.pdf",
    }
    payload.update(overrides)
    return payload


def test_results_returns_both_seeded_demo_invoices(client: TestClient) -> None:
    resp = client.get("/api/v1/processing/results")
    assert resp.status_code == 200
    body = resp.json()
    assert [r["invoice_id"] for r in body] == ["inv-demo-clean-1", "inv-demo-flagged-1"]


def test_clean_invoice_goes_straight_through_with_a_payment_instruction(
    client: TestClient,
) -> None:
    body = client.get("/api/v1/processing/results").json()
    clean = next(r for r in body if r["invoice_id"] == "inv-demo-clean-1")

    assert clean["decision"] == "straight_through"
    instruction = clean["payment_instruction"]
    assert instruction is not None
    # FR-085: bank reference is the vendor master's, never anything from the invoice.
    assert instruction["vendor_bank_account"] == "HDFC0001234-000111222333"


def test_flagged_invoice_is_an_exception_with_no_payment_instruction(client: TestClient) -> None:
    body = client.get("/api/v1/processing/results").json()
    flagged = next(r for r in body if r["invoice_id"] == "inv-demo-flagged-1")

    assert flagged["decision"] == "exception"
    assert flagged["payment_instruction"] is None
    assert any(issue["rule_id"] == "BR-03" for issue in flagged["issues"])


def test_results_are_stable_across_repeated_requests(client: TestClient) -> None:
    # Regression guard: a second GET must not re-run hand_off with a fresh timestamp and
    # trip AlreadyHandedOffError against the first request's cached instruction.
    first = client.get("/api/v1/processing/results")
    second = client.get("/api/v1/processing/results")
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()


def test_upload_a_clean_invoice_goes_straight_through(client: TestClient) -> None:
    resp = client.post("/api/v1/processing/process", json=valid_upload())
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "straight_through"
    assert body["source_filename"] == "beta-traders-inv-9001.pdf"
    assert body["payment_instruction"]["vendor_bank_account"] == "ICIC0009999-111222333"


def test_uploaded_invoice_appears_in_the_results_list(client: TestClient) -> None:
    client.post("/api/v1/processing/process", json=valid_upload())
    body = client.get("/api/v1/processing/results").json()
    assert len(body) == 3
    assert body[-1]["invoice_number"] == "INV-9001"


def test_upload_new_vendor_never_goes_straight_through(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/processing/process",
        json=valid_upload(vendor_invoices_processed=0),
    )
    body = resp.json()
    assert body["decision"] != "straight_through"
    assert body["reason"] == "policy:new_vendor"


def test_upload_low_confidence_needs_review(client: TestClient) -> None:
    resp = client.post("/api/v1/processing/process", json=valid_upload(confidence=0.75))
    body = resp.json()
    assert body["decision"] in ("review", "full_review")
    assert body["payment_instruction"] is None


def test_uploading_the_same_invoice_twice_trips_the_duplicate_rule(client: TestClient) -> None:
    first = client.post("/api/v1/processing/process", json=valid_upload()).json()
    second = client.post("/api/v1/processing/process", json=valid_upload()).json()

    assert first["decision"] == "straight_through"
    assert second["decision"] == "exception"
    assert any(issue["rule_id"] == "BR-04" for issue in second["issues"])


def test_uploading_against_the_seeded_demo_vendor_tax_id_is_also_a_duplicate(
    client: TestClient,
) -> None:
    # The duplicate store isn't just for uploads-against-uploads — it's seeded from the
    # two demo invoices too, so this is a real, whole-process duplicate check.
    client.get("/api/v1/processing/results")  # ensure the demo invoices are seeded first
    resp = client.post(
        "/api/v1/processing/process",
        json=valid_upload(
            vendor_tax_id="27AAPFU0939F1ZV",  # Acme's GSTIN, the seeded clean invoice's
            invoice_number="INV-1001",  # same invoice number as the seeded clean invoice
        ),
    )
    body = resp.json()
    assert body["decision"] == "exception"
    assert any(issue["rule_id"] == "BR-04" for issue in body["issues"])


def test_duplicate_store_records_and_reports_the_same_pair() -> None:
    store = _DuplicateStore(seen=set())
    assert not store.exact_duplicate_exists("TAX1", "INV-1")
    store.record("TAX1", "INV-1")
    assert store.exact_duplicate_exists("TAX1", "INV-1")


def test_processing_store_results_are_lazy_and_memoized() -> None:
    store = _ProcessingStore()
    assert store._results is None
    first = store.list_results()
    assert store._results is first
    assert store.list_results() is first


def test_mock_payment_instruction_adapter_still_importable_here() -> None:
    # Regression guard for the router's re-export path used above.
    assert MockPaymentInstructionAdapter().get("nothing") is None


# --- POST /extract: real Document Intelligence, faked at the SDK client boundary ---


def _ocr_field(name: str, value: object, confidence: float = 0.97) -> ExtractedField:
    return ExtractedField(
        name=name,
        value=value,
        confidence=confidence,
        page=1,
        region=BoundingRegion(page=1, polygon=(0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0)),
    )


def clean_extracted_invoice(invoice_id: str, invoice_number: str, tax_id: str | None) -> Invoice:
    fields = {
        "vendor_name": _ocr_field("vendor_name", "Zeta Print Co"),
        "invoice_number": _ocr_field("invoice_number", invoice_number),
        "invoice_date": _ocr_field("invoice_date", date(2026, 8, 10)),
        "currency": _ocr_field("currency", "INR"),
        "subtotal": _ocr_field("subtotal", Decimal("500.00")),
        "tax_total": _ocr_field("tax_total", Decimal("90.00")),
        "total": _ocr_field("total", Decimal("590.00")),
        "amount_due": _ocr_field("amount_due", Decimal("590.00")),
    }
    if tax_id:
        fields["vendor_tax_id"] = _ocr_field("vendor_tax_id", tax_id)
    return Invoice(invoice_id=invoice_id, document_id=invoice_id, fields=fields)


class FakeExtractor:
    """Stands in for the real `DocumentIntelligenceExtractor` at the FastAPI dependency
    boundary — proves the router's error handling and result mapping, not the SDK
    integration itself (that's test_document_intelligence.py's job)."""

    def __init__(self, outcome: ExtractionResult | Exception) -> None:
        self.outcome = outcome
        self.last_request: ExtractionRequest | None = None

    def extract(self, request: ExtractionRequest) -> ExtractionResult:
        self.last_request = request
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def override_extractor(client: TestClient, extractor: FakeExtractor) -> None:
    app.dependency_overrides[get_document_intelligence_extractor] = lambda: extractor


def test_extract_sends_the_uploaded_bytes_to_the_extractor(client: TestClient) -> None:
    fake = FakeExtractor(
        ExtractionResult(
            invoice=clean_extracted_invoice("inv-x", "INV-7001", "27AAPFU0939F1ZV"),
            model_id="prebuilt-invoice",
            model_version="2024-11-30",
            raw_response={},
        )
    )
    override_extractor(client, fake)

    resp = client.post(
        "/api/v1/processing/extract",
        files={"file": ("zeta-inv-7001.pdf", b"%PDF-1.4 real bytes", "application/pdf")},
    )

    assert resp.status_code == 200
    assert fake.last_request is not None
    assert fake.last_request.content == b"%PDF-1.4 real bytes"
    body = resp.json()
    assert body["extraction_method"] == "azure_document_intelligence"
    assert body["source_filename"] == "zeta-inv-7001.pdf"
    assert body["invoice_number"] == "INV-7001"
    assert body["vendor_name"] == "Zeta Print Co"


def test_extract_first_invoice_from_a_vendor_is_never_straight_through(
    client: TestClient,
) -> None:
    # AC-01.3: no processing history for this vendor actually exists — this isn't a
    # threshold tuned low, it's a database of one.
    fake = FakeExtractor(
        ExtractionResult(
            invoice=clean_extracted_invoice("inv-x", "INV-7001", "27AAPFU0939F1ZV"),
            model_id="prebuilt-invoice",
            model_version="2024-11-30",
            raw_response={},
        )
    )
    override_extractor(client, fake)

    body = client.post(
        "/api/v1/processing/extract",
        files={"file": ("zeta-inv-7001.pdf", b"fake pdf", "application/pdf")},
    ).json()

    assert body["decision"] != "straight_through"
    assert body["reason"] == "policy:new_vendor"
    assert body["payment_instruction"] is None


def test_extract_second_invoice_from_same_vendor_can_go_straight_through_with_no_bank_account(
    client: TestClient,
) -> None:
    fake = FakeExtractor(
        ExtractionResult(
            invoice=clean_extracted_invoice("inv-x", "INV-7001", "27AAPFU0939F1ZV"),
            model_id="prebuilt-invoice",
            model_version="2024-11-30",
            raw_response={},
        )
    )
    override_extractor(client, fake)
    client.post(
        "/api/v1/processing/extract",
        files={"file": ("first.pdf", b"fake pdf", "application/pdf")},
    )

    fake.outcome = ExtractionResult(
        invoice=clean_extracted_invoice("inv-y", "INV-7002", "27AAPFU0939F1ZV"),
        model_id="prebuilt-invoice",
        model_version="2024-11-30",
        raw_response={},
    )
    second = client.post(
        "/api/v1/processing/extract",
        files={"file": ("second.pdf", b"fake pdf", "application/pdf")},
    ).json()

    assert second["decision"] == "straight_through"
    # FR-085: never sourced from the invoice, and there's no real vendor master yet to
    # supply a trustworthy value — so this is honestly empty, not fabricated.
    assert second["payment_instruction"]["vendor_bank_account"] is None


def test_extract_maps_permanent_extraction_error_to_422(client: TestClient) -> None:
    override_extractor(client, FakeExtractor(PermanentExtractionError("bad file")))

    resp = client.post(
        "/api/v1/processing/extract",
        files={"file": ("bad.pdf", b"not a pdf", "application/pdf")},
    )

    assert resp.status_code == 422
    assert "bad file" in resp.json()["detail"]


def test_extract_maps_transient_extraction_error_to_503(client: TestClient) -> None:
    override_extractor(client, FakeExtractor(TransientExtractionError("throttled")))

    resp = client.post(
        "/api/v1/processing/extract",
        files={"file": ("ok.pdf", b"pdf bytes", "application/pdf")},
    )

    assert resp.status_code == 503
    assert "throttled" in resp.json()["detail"]


def test_extracted_invoice_appears_in_the_results_list(client: TestClient) -> None:
    override_extractor(
        client,
        FakeExtractor(
            ExtractionResult(
                invoice=clean_extracted_invoice("inv-x", "INV-7001", None),
                model_id="prebuilt-invoice",
                model_version="2024-11-30",
                raw_response={},
            )
        ),
    )
    client.post(
        "/api/v1/processing/extract",
        files={"file": ("no-tax-id.pdf", b"pdf bytes", "application/pdf")},
    )

    body = client.get("/api/v1/processing/results").json()
    assert len(body) == 3
    assert body[-1]["extraction_method"] == "azure_document_intelligence"


def test_vendor_registry_resolves_by_tax_id_when_present() -> None:
    registry = _VendorRegistry()
    first = registry.resolve_or_create("Zeta Print Co", "TAX-1")
    assert first.invoices_processed == 0
    second = registry.resolve_or_create("Zeta Print Co (renamed)", "TAX-1")
    assert second.vendor_id == first.vendor_id
    assert second.invoices_processed == 1


def test_vendor_registry_falls_back_to_name_when_no_tax_id() -> None:
    registry = _VendorRegistry()
    first = registry.resolve_or_create("No Tax Id Vendor", None)
    second = registry.resolve_or_create("No Tax Id Vendor", None)
    assert second.vendor_id == first.vendor_id
    assert second.invoices_processed == 1


def test_module_level_store_singleton_exists() -> None:
    assert isinstance(module_store, _ProcessingStore)
