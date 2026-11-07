"""HTTP-level tests for the processing-results and invoice-upload endpoints. Same
TestClient, no-live-server pattern as test_review_api.py.

T-047's Cosmos wiring landed in api/routers/processing.py: vendor resolution, invoice
persistence, duplicate detection, and audit logging all go through real repository
Protocols now, not in-memory placeholders. These tests override every one of those five
FastAPI dependencies with small in-memory fakes — same reasoning as
tests/unit/fakes.py's FakeContainer: hermetic and fast, no live Cosmos account touched.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from azure.cosmos.exceptions import CosmosResourceExistsError
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
from api.routers.processing import (
    MockPaymentInstructionAdapter,
    _ProcessingStore,
    _Repos,
    _resolve_or_create_vendor,
)
from api.routers.processing import _store as module_store
from core.extraction.base import (
    ExtractionRequest,
    ExtractionResult,
    PermanentExtractionError,
    TransientExtractionError,
)
from core.models import (
    BoundingRegion,
    ExtractedField,
    Invoice,
    ReviewAssignment,
    ReviewOutcome,
    Vendor,
)

# Checksum-valid GSTINs, deliberately *different* from processing.py's own
# _VALID_GSTIN/the seeded demo vendor's — sharing one would mean a "new vendor" OCR test
# resolves to the already-50-times-processed demo vendor once vendor resolution is a
# single shared repository instead of two unrelated in-memory registries. Two distinct
# ones, not one shared between them, so "Zeta Print Co" and "Beta Traders" stay separate
# vendors from each other too, not just from Acme.
_OTHER_VALID_GSTIN = "29ZETAP1234F1ZF"  # Zeta Print Co (OCR-path tests)
_BETA_TRADERS_GSTIN = "19BETAT5678K1Z1"  # Beta Traders (valid_upload()'s default)


class FakeVendorRepo:
    def __init__(self) -> None:
        self._by_id: dict[str, Vendor] = {}
        self._by_tax_id: dict[str, str] = {}

    def get(self, vendor_id: str) -> Vendor | None:
        return self._by_id.get(vendor_id)

    def find_by_tax_id(self, tax_id: str) -> Vendor | None:
        vendor_id = self._by_tax_id.get(tax_id)
        return self._by_id.get(vendor_id) if vendor_id else None

    def active_vendors(self) -> list[Vendor]:
        return [v for v in self._by_id.values() if v.is_active]

    def save(self, vendor: Vendor) -> None:
        # Mirrors CosmosVendorRepository.save()'s one-tax-id-one-vendor-id constraint
        # (vendor_tax_ids container) — a silently-permissive fake here would have hidden
        # exactly the bug this constraint caught live: a second vendor trying to claim an
        # already-claimed tax_id under a different vendor_id.
        if vendor.tax_id:
            owner = self._by_tax_id.get(vendor.tax_id)
            if owner is not None and owner != vendor.vendor_id:
                raise ValueError(f"tax id {vendor.tax_id!r} is already claimed by vendor {owner!r}")
            self._by_tax_id[vendor.tax_id] = vendor.vendor_id
        self._by_id[vendor.vendor_id] = vendor


class FakeInvoiceRepo:
    """Satisfies InvoiceRepository, DuplicateLookup, and InvoiceAdmin — same
    one-class-for-three-protocols shape as the real CosmosInvoiceRepository, and the same
    single shared instance is handed to all three FastAPI overrides below."""

    def __init__(self) -> None:
        self._by_id: dict[str, Invoice] = {}

    def get(self, invoice_id: str) -> Invoice | None:
        return self._by_id.get(invoice_id)

    def save(self, invoice: Invoice) -> None:
        # Mirrors the invoices container's real uniqueKeyPolicy — /invoice_number scoped
        # to the vendor_id partition. A silently-permissive fake here would have hidden
        # the batch race this constraint caught live: two concurrent saves for the same
        # (vendor, invoice_number) under two different invoice_ids.
        if invoice.invoice_number is not None:
            for other in self._by_id.values():
                if (
                    other.invoice_id != invoice.invoice_id
                    and other.vendor_id == invoice.vendor_id
                    and other.invoice_number == invoice.invoice_number
                ):
                    raise CosmosResourceExistsError(
                        message=(
                            f"unique key conflict on invoice_number within partition "
                            f"{invoice.vendor_id!r}"
                        )
                    )
        self._by_id[invoice.invoice_id] = invoice

    def get_by_content_hash(self, content_hash: str) -> Invoice | None:
        for invoice in self._by_id.values():
            if invoice.content_hash == content_hash:
                return invoice
        return None

    def delete(self, invoice_id: str) -> None:
        self._by_id.pop(invoice_id, None)

    def exact_duplicate_exists(
        self, vendor_tax_id: str, invoice_number: str, exclude_invoice_id: str | None = None
    ) -> bool:
        return any(
            inv.vendor_tax_id == vendor_tax_id
            and inv.invoice_number == invoice_number
            and inv.invoice_id != exclude_invoice_id
            for inv in self._by_id.values()
        )

    def find_near_duplicates(
        self, vendor_id: str, amount: Decimal, invoice_date: date, window_days: int
    ) -> list[str]:
        return []


class FakeAuditLog:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def record(self, **kwargs: Any) -> None:
        self.entries.append(kwargs)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    # Swap in a fresh store per test — the module-level singleton would otherwise leak
    # seeded state (including persisted vendors/invoices) across tests in the same
    # process. Same reasoning extends to every fake repo below: fresh per test.
    import api.auth as auth_module
    import api.routers.processing as processing_module

    original = processing_module._store
    processing_module._store = _ProcessingStore()
    original_sessions = auth_module._sessions
    auth_module._sessions = SessionStore()

    invoice_repo = FakeInvoiceRepo()
    overrides = {
        get_vendor_repo: FakeVendorRepo(),
        get_duplicate_lookup: invoice_repo,
        get_invoice_repo: invoice_repo,
        get_invoice_admin: invoice_repo,
        get_audit_log_writer: FakeAuditLog(),
        get_review_repo: InMemoryReviewAssignmentRepository(),
    }

    # A `lambda fake=fake: fake` closure per iteration looks like the standard fix for
    # the late-binding loop-variable trap, and *is* one for plain Python — but FastAPI's
    # dependency-override resolution inspects each override callable's own signature, and
    # a lambda's default-valued parameter gets re-resolved on every request rather than
    # captured once. Observed directly: two POSTs in the same test each got a *different*
    # FakeInvoiceRepo instance back, silently breaking the duplicate check across them. A
    # named factory sidesteps it — no parameter on the returned callable to re-resolve.
    def _make_override(fake: object) -> Any:
        def _override() -> object:
            return fake

        return _override

    for dependency, fake in overrides.items():
        app.dependency_overrides[dependency] = _make_override(fake)

    # This router now requires a session on every endpoint (dependencies=[Depends(
    # get_session)] in api/routers/processing.py) — authenticate once here and set it as
    # a default header, so every existing test call below keeps working unchanged rather
    # than threading a token through each of them individually. Tests that specifically
    # exercise the unauthenticated/bad-token path override this per-call.
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
    processing_module._store = original
    auth_module._sessions = original_sessions


def valid_upload(**overrides: object) -> dict:
    payload = {
        "vendor_name": "Beta Traders",
        "vendor_tax_id": _BETA_TRADERS_GSTIN,
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


def test_results_requires_a_session(client: TestClient) -> None:
    # This whole router requires a session now (dependencies=[Depends(get_session)]) —
    # not just /results/rejected. Empty string overrides the fixture's default
    # Authorization header for this one call, simulating no session at all.
    resp = client.get("/api/v1/processing/results", headers={"Authorization": ""})
    assert resp.status_code == 401


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


def test_results_never_includes_a_rejected_invoices_details(client: TestClient) -> None:
    client.get("/api/v1/processing/results")  # ensure demo invoices are seeded first
    review_repo = app.dependency_overrides[get_review_repo]()
    review_repo.save(
        ReviewAssignment(invoice_id="inv-demo-flagged-1", outcome=ReviewOutcome.REJECTED)
    )

    body = client.get("/api/v1/processing/results").json()

    assert "inv-demo-flagged-1" not in [r["invoice_id"] for r in body]
    assert "inv-demo-clean-1" in [r["invoice_id"] for r in body]


def test_results_rejected_requires_a_session(client: TestClient) -> None:
    # Empty string overrides the fixture's default Authorization header for this one
    # call — simulates no session at all, same as a real request with no header.
    resp = client.get("/api/v1/processing/results/rejected", headers={"Authorization": ""})
    assert resp.status_code == 401


def test_results_rejected_rejects_a_garbage_token(client: TestClient) -> None:
    resp = client.get(
        "/api/v1/processing/results/rejected", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert resp.status_code == 401


def test_results_rejected_returns_only_rejected_invoices_with_a_valid_session(
    client: TestClient,
) -> None:
    client.get("/api/v1/processing/results")  # the fixture's default session is enough
    review_repo = app.dependency_overrides[get_review_repo]()
    review_repo.save(
        ReviewAssignment(invoice_id="inv-demo-flagged-1", outcome=ReviewOutcome.REJECTED)
    )

    resp = client.get("/api/v1/processing/results/rejected")
    assert resp.status_code == 200
    body = resp.json()
    assert [r["invoice_id"] for r in body] == ["inv-demo-flagged-1"]


def test_summary_counts_accepted_rejected_and_pending(client: TestClient) -> None:
    client.get("/api/v1/processing/results")  # seeds inv-demo-clean-1 (straight_through)
    # and inv-demo-flagged-1 (exception, pending review)
    review_repo = app.dependency_overrides[get_review_repo]()
    review_repo.save(
        ReviewAssignment(invoice_id="inv-demo-flagged-1", outcome=ReviewOutcome.REJECTED)
    )

    body = client.get("/api/v1/processing/summary").json()

    assert body["total"] == 2
    assert body["accepted_count"] == 1  # inv-demo-clean-1
    assert body["rejected_count"] == 1  # inv-demo-flagged-1
    assert body["pending_count"] == 0


def test_summary_counts_a_hard_failure_as_urgent(client: TestClient) -> None:
    client.get("/api/v1/processing/results")  # inv-demo-flagged-1 is decision=exception

    body = client.get("/api/v1/processing/summary").json()

    assert body["urgent_count"] >= 1


def test_summary_does_not_count_an_already_approved_exception_as_urgent(
    client: TestClient,
) -> None:
    """Regression test: decision == "exception" reflects the router's original call, not
    whether a human has since resolved it. Approving an exception invoice must drop it
    out of urgent_count — otherwise a resolved invoice counts as urgent forever."""
    client.get("/api/v1/processing/results")  # seeds inv-demo-flagged-1 (exception)
    review_repo = app.dependency_overrides[get_review_repo]()
    review_repo.save(
        ReviewAssignment(invoice_id="inv-demo-flagged-1", outcome=ReviewOutcome.APPROVED)
    )

    body = client.get("/api/v1/processing/summary").json()

    assert body["urgent_count"] == 0
    assert body["pending_count"] == 0
    assert body["accepted_count"] == 2  # both seeded demo invoices now accepted


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


def test_fake_invoice_repo_save_enforces_the_same_uniqueness_cosmos_does() -> None:
    # Direct test of the fake's own fidelity — a silently-permissive fake here would be
    # exactly what let the live batch race (two concurrent saves for the same vendor +
    # invoice_number, two different invoice_ids) go uncaught by the unit suite.
    repo = FakeInvoiceRepo()
    first = clean_extracted_invoice("inv-a", "INV-RACE", None)
    first.vendor_id = "v-1"
    second = clean_extracted_invoice("inv-b", "INV-RACE", None)
    second.vendor_id = "v-1"
    repo.save(first)

    with pytest.raises(CosmosResourceExistsError):
        repo.save(second)


def test_a_concurrent_save_conflict_becomes_a_clean_409_not_a_crash(client: TestClient) -> None:
    # Simulates the exact failure mode from the live batch test: BR-04's pre-save check
    # passes (nothing saved yet from either side of the race), then the actual write
    # loses to a concurrent one at the database's own uniqueness constraint. The fix is
    # in _process_and_persist's except CosmosResourceExistsError — this proves it
    # surfaces as 409 with a clear reason, not an unhandled 500.
    class AlwaysConflictsInvoiceRepo(FakeInvoiceRepo):
        def save(self, invoice: Invoice) -> None:
            raise CosmosResourceExistsError(message="unique key conflict on invoice_number")

    app.dependency_overrides[get_invoice_repo] = lambda: AlwaysConflictsInvoiceRepo()

    resp = client.post("/api/v1/processing/process", json=valid_upload())

    assert resp.status_code == 409
    assert "concurrent request" in resp.json()["detail"]


def test_uploading_against_the_seeded_demo_vendor_tax_id_is_also_a_duplicate(
    client: TestClient,
) -> None:
    # The duplicate check isn't just for uploads-against-uploads — every processed
    # invoice is genuinely saved (T-047), seeded demo invoices included, so this is a
    # real, whole-process duplicate check against persisted data.
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


def test_deleting_a_duplicate_lets_the_same_invoice_number_be_resubmitted(
    client: TestClient,
) -> None:
    # This is the whole point of T-047's delete wiring: removing a duplicate from the
    # dashboard also removes it from the store BR-04 actually checks, so it's genuinely
    # gone, not just hidden.
    first = client.post("/api/v1/processing/process", json=valid_upload()).json()
    assert first["decision"] == "straight_through"

    delete_resp = client.delete(f"/api/v1/processing/results/{first['invoice_id']}")
    assert delete_resp.status_code == 204

    second = client.post("/api/v1/processing/process", json=valid_upload()).json()
    assert second["decision"] == "straight_through"  # not rejected as a duplicate anymore


def test_submitting_a_different_invoice_number_for_the_same_vendor_tax_id_does_not_500(
    client: TestClient,
) -> None:
    # Regression: two "test scenario" submissions sharing a vendor_tax_id used to each
    # build a Vendor with a fresh random vendor_id — fine against the old in-memory
    # _VendorRegistry, but CosmosVendorRepository enforces one tax_id -> one vendor_id
    # forever, so the second save() raised an uncaught ValueError -> 500. Caught live,
    # not by the unit suite, because the fake vendor repo didn't enforce that constraint
    # either at the time — see FakeVendorRepo.save()'s comment.
    first = client.post("/api/v1/processing/process", json=valid_upload(invoice_number="INV-9001"))
    second = client.post("/api/v1/processing/process", json=valid_upload(invoice_number="INV-9002"))
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["decision"] == "straight_through"  # a genuinely different invoice


def test_deleting_an_unknown_invoice_id_is_still_a_204(client: TestClient) -> None:
    resp = client.delete("/api/v1/processing/results/inv-does-not-exist")
    assert resp.status_code == 204


def test_processing_store_results_are_lazy_and_memoized() -> None:
    store = _ProcessingStore()
    repos = _Repos(
        vendors=FakeVendorRepo(),
        duplicates=(invoice_repo := FakeInvoiceRepo()),
        invoices=invoice_repo,
        audit_log=FakeAuditLog(),
    )
    assert store._results is None
    first = store.list_results(repos)
    assert store._results is first
    assert store.list_results(repos) is first


def test_mock_payment_instruction_adapter_still_importable_here() -> None:
    # Regression guard for the router's re-export path used above.
    assert MockPaymentInstructionAdapter().get("nothing") is None


def test_resolve_or_create_vendor_resolves_by_tax_id_when_present() -> None:
    repo = FakeVendorRepo()
    first = _resolve_or_create_vendor(repo, "Zeta Print Co", _OTHER_VALID_GSTIN)
    assert first.invoices_processed == 0
    second = _resolve_or_create_vendor(repo, "Zeta Print Co (renamed)", _OTHER_VALID_GSTIN)
    assert second.vendor_id == first.vendor_id
    assert second.invoices_processed == 1


def test_resolve_or_create_vendor_always_creates_new_when_no_tax_id() -> None:
    # Cosmos's vendor_tax_ids container is tax-id-keyed only (see
    # _resolve_or_create_vendor's docstring) — there's no name-based lookup the way the
    # old in-memory _VendorRegistry had, so two calls with the same name and no tax id
    # are genuinely two different vendors now.
    repo = FakeVendorRepo()
    first = _resolve_or_create_vendor(repo, "No Tax Id Vendor", None)
    second = _resolve_or_create_vendor(repo, "No Tax Id Vendor", None)
    assert second.vendor_id != first.vendor_id


def test_resolve_or_create_vendor_recovers_when_it_loses_the_tax_id_race() -> None:
    # Reproduces the exact failure from the live batch test: the same vendor extracted
    # from two files in one POST /extract/batch, processed concurrently. Both workers'
    # find_by_tax_id() miss (nobody's claimed it yet); by the time the *second* one's
    # save() runs, the first has already won — CosmosVendorRepository.save() raises
    # ValueError in that case (see vendors.py's _claim_tax_id). This proves the caller
    # recovers by re-resolving to the winner instead of crashing the whole file.
    class RaceLostVendorRepo(FakeVendorRepo):
        def __init__(self) -> None:
            super().__init__()
            self.find_calls = 0

        def find_by_tax_id(self, tax_id: str) -> Vendor | None:
            self.find_calls += 1
            if self.find_calls == 1:
                return None  # the TOCTOU gap: this runs before the winner's save() lands
            return super().find_by_tax_id(tax_id)

        def save(self, vendor: Vendor) -> None:
            owner = self._by_tax_id.get(vendor.tax_id)
            if owner is not None and owner != vendor.vendor_id:
                raise ValueError(f"tax id {vendor.tax_id!r} is already claimed by vendor {owner!r}")
            super().save(vendor)

    repo = RaceLostVendorRepo()
    winner = Vendor(
        vendor_id="v-winner",
        name="Zeta Print Co",
        normalised_name="zeta print co",
        tax_id=_OTHER_VALID_GSTIN,
        invoices_processed=3,
    )
    FakeVendorRepo.save(repo, winner)  # seed directly, bypassing the overridden find

    result = _resolve_or_create_vendor(repo, "Zeta Print Co (racing copy)", _OTHER_VALID_GSTIN)

    assert result.vendor_id == "v-winner"
    assert result.invoices_processed == 4


def test_module_level_store_singleton_exists() -> None:
    assert isinstance(module_store, _ProcessingStore)


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
        self.calls: list[ExtractionRequest] = []

    def extract(self, request: ExtractionRequest) -> ExtractionResult:
        self.last_request = request
        self.calls.append(request)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def override_extractor(client: TestClient, extractor: FakeExtractor | PerFileFakeExtractor) -> None:
    app.dependency_overrides[get_document_intelligence_extractor] = lambda: extractor


def test_extract_sends_the_uploaded_bytes_to_the_extractor(client: TestClient) -> None:
    fake = FakeExtractor(
        ExtractionResult(
            invoice=clean_extracted_invoice("inv-x", "INV-7001", _OTHER_VALID_GSTIN),
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


def test_extract_reuses_the_cache_on_identical_bytes_without_calling_the_extractor_again(
    client: TestClient,
) -> None:
    fake = FakeExtractor(
        ExtractionResult(
            invoice=clean_extracted_invoice("inv-x", "INV-7001", _OTHER_VALID_GSTIN),
            model_id="prebuilt-invoice",
            model_version="2024-11-30",
            raw_response={},
        )
    )
    override_extractor(client, fake)
    same_bytes = b"%PDF-1.4 identical bytes, uploaded twice"

    first = client.post(
        "/api/v1/processing/extract",
        files={"file": ("copy1.pdf", same_bytes, "application/pdf")},
    ).json()
    second = client.post(
        "/api/v1/processing/extract",
        files={"file": ("copy2.pdf", same_bytes, "application/pdf")},
    ).json()

    assert first["extraction_method"] == "azure_document_intelligence"
    assert second["extraction_method"] == "cache_reuse"
    assert second["invoice_number"] == "INV-7001"  # fields reused from the cached extraction
    assert len(fake.calls) == 1  # the extractor genuinely only ran once


def test_extract_first_invoice_from_a_vendor_is_never_straight_through(
    client: TestClient,
) -> None:
    # AC-01.3: no processing history for this vendor actually exists — this isn't a
    # threshold tuned low, it's a database of one.
    fake = FakeExtractor(
        ExtractionResult(
            invoice=clean_extracted_invoice("inv-x", "INV-7001", _OTHER_VALID_GSTIN),
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
            invoice=clean_extracted_invoice("inv-x", "INV-7001", _OTHER_VALID_GSTIN),
            model_id="prebuilt-invoice",
            model_version="2024-11-30",
            raw_response={},
        )
    )
    override_extractor(client, fake)
    client.post(
        "/api/v1/processing/extract",
        # Distinct bytes per call, not the same b"fake pdf" twice — T-044's cache is
        # genuinely wired now (this test predates that), and identical bytes on the
        # second call would be a real cache hit reusing the first extraction's fields
        # instead of exercising fake.outcome's second invoice at all.
        files={"file": ("first.pdf", b"fake pdf - first", "application/pdf")},
    )

    fake.outcome = ExtractionResult(
        invoice=clean_extracted_invoice("inv-y", "INV-7002", _OTHER_VALID_GSTIN),
        model_id="prebuilt-invoice",
        model_version="2024-11-30",
        raw_response={},
    )
    second = client.post(
        "/api/v1/processing/extract",
        files={"file": ("second.pdf", b"fake pdf - second", "application/pdf")},
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


def _login_quota_limited_user(client: TestClient, *, quota: int) -> str:
    from datetime import date

    from api.auth import UserRecord, hash_password, upsert_user

    upsert_user(
        UserRecord(
            username="demo-quota",
            role="ap",
            password_hash=hash_password("demo-pw"),
            doc_quota=quota,
            docs_used=0,
            valid_until=None,
            created_at=date.today(),
        )
    )
    resp = client.post("/api/v1/auth/login", json={"username": "demo-quota", "password": "demo-pw"})
    token: str = resp.json()["token"]
    return token


def test_extract_is_refused_once_the_signed_in_users_quota_is_used_up(
    client: TestClient,
) -> None:
    token = _login_quota_limited_user(client, quota=1)
    headers = {"Authorization": f"Bearer {token}"}
    override_extractor(
        client,
        FakeExtractor(
            ExtractionResult(
                invoice=clean_extracted_invoice("inv-quota-1", "INV-9101", None),
                model_id="prebuilt-invoice",
                model_version="2024-11-30",
                raw_response={},
            )
        ),
    )

    first = client.post(
        "/api/v1/processing/extract",
        files={"file": ("first.pdf", b"pdf bytes", "application/pdf")},
        headers=headers,
    )
    assert first.status_code == 200

    second = client.post(
        "/api/v1/processing/extract",
        files={"file": ("second.pdf", b"pdf bytes", "application/pdf")},
        headers=headers,
    )
    assert second.status_code == 403
    assert "limit reached" in second.json()["detail"].lower()


# --- POST /extract/batch: bounded-concurrency multi-file upload ---


class PerFileFakeExtractor:
    """Unlike FakeExtractor's single outcome for every call, a batch test needs different
    files to behave differently — one throttled, one fine, say. Keyed by blob_uri, which
    extract_and_process builds as f"local-upload://{filename}", so the filename a test
    uploads under is what selects its outcome."""

    def __init__(self, outcomes: dict[str, ExtractionResult | Exception]) -> None:
        self.outcomes = outcomes
        self.calls: list[ExtractionRequest] = []

    def extract(self, request: ExtractionRequest) -> ExtractionResult:
        self.calls.append(request)
        outcome = self.outcomes[request.blob_uri]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _batch_files(*filenames: str) -> list[tuple[str, tuple[str, bytes, str]]]:
    # Content keyed by filename, not a shared b"pdf bytes" for every entry — T-044's
    # cache is genuinely wired now, so identical bytes across "different" files would
    # make every file after the first a real cache hit instead of its own extraction.
    return [
        ("files", (name, f"pdf bytes for {name}".encode(), "application/pdf")) for name in filenames
    ]


def test_batch_extracts_every_file_and_all_appear_in_results(client: TestClient) -> None:
    filenames = [f"vendor-{i}.pdf" for i in range(5)]
    fake = PerFileFakeExtractor(
        {
            f"local-upload://{name}": ExtractionResult(
                invoice=clean_extracted_invoice(f"inv-{i}", f"INV-{7000 + i}", None),
                model_id="prebuilt-invoice",
                model_version="2024-11-30",
                raw_response={},
            )
            for i, name in enumerate(filenames)
        }
    )
    override_extractor(client, fake)

    resp = client.post("/api/v1/processing/extract/batch", files=_batch_files(*filenames))

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 5
    assert all(item["status"] == "success" for item in body)
    assert {item["filename"] for item in body} == set(filenames)
    assert len(fake.calls) == 5  # every file genuinely reached the extractor once

    results = client.get("/api/v1/processing/results").json()
    assert len(results) == 2 + 5  # seeded demo invoices + the whole batch, none lost


def test_batch_is_refused_entirely_when_it_would_exceed_the_signed_in_users_quota(
    client: TestClient,
) -> None:
    # Checked against the *whole* batch size up front — a 403 with nothing extracted at
    # all, not a partial run.
    token = _login_quota_limited_user(client, quota=2)
    headers = {"Authorization": f"Bearer {token}"}
    filenames = [f"vendor-{i}.pdf" for i in range(3)]
    fake = PerFileFakeExtractor(
        {
            f"local-upload://{name}": ExtractionResult(
                invoice=clean_extracted_invoice(f"inv-quota-batch-{i}", f"INV-{8000 + i}", None),
                model_id="prebuilt-invoice",
                model_version="2024-11-30",
                raw_response={},
            )
            for i, name in enumerate(filenames)
        }
    )
    override_extractor(client, fake)

    resp = client.post(
        "/api/v1/processing/extract/batch", files=_batch_files(*filenames), headers=headers
    )

    assert resp.status_code == 403
    assert len(fake.calls) == 0  # nothing was even attempted


def test_batch_reports_one_file_failing_without_sinking_the_rest(client: TestClient) -> None:
    fake = PerFileFakeExtractor(
        {
            "local-upload://good.pdf": ExtractionResult(
                invoice=clean_extracted_invoice("inv-good", "INV-8001", None),
                model_id="prebuilt-invoice",
                model_version="2024-11-30",
                raw_response={},
            ),
            "local-upload://bad.pdf": PermanentExtractionError("not an invoice"),
        }
    )
    override_extractor(client, fake)

    resp = client.post(
        "/api/v1/processing/extract/batch", files=_batch_files("good.pdf", "bad.pdf")
    )

    assert resp.status_code == 200
    body = {item["filename"]: item for item in resp.json()}
    assert body["good.pdf"]["status"] == "success"
    assert body["good.pdf"]["result"]["invoice_number"] == "INV-8001"
    assert body["bad.pdf"]["status"] == "error"
    assert "not an invoice" in body["bad.pdf"]["error"]
    assert body["bad.pdf"]["result"] is None
