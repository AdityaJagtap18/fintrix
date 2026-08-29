"""Tests the Document Intelligence adapter's field-mapping and error-translation logic
against real `azure-ai-documentintelligence` SDK model objects — built directly in these
tests, not against a live resource. This is the same trust level as any other adapter
test: it proves the mapping is correct for a given SDK response shape; it cannot prove the
real service actually returns that shape (only an integration test against a sandbox
resource, T-054, can do that).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from azure.ai.documentintelligence.models import (
    AddressValue,
    AnalyzedDocument,
    AnalyzeResult,
    BoundingRegion,
    CurrencyValue,
    DocumentField,
)
from azure.core.credentials import AccessToken
from azure.core.exceptions import ClientAuthenticationError, HttpResponseError, ServiceRequestError

from core.extraction.base import (
    ClassificationResult,
    DocumentType,
    ExtractionRequest,
    PermanentExtractionError,
    TransientExtractionError,
)
from core.extraction.document_intelligence import DocumentIntelligenceExtractor
from core.models import FieldSource


class FakeCredential:
    def get_token(self, *scopes: str, **kwargs: object) -> AccessToken:
        return AccessToken("fake-token", 9_999_999_999)


class FakePoller:
    def __init__(self, outcome: AnalyzeResult | Exception) -> None:
        self._outcome = outcome

    def result(self) -> AnalyzeResult:
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


def make_extractor(outcome: AnalyzeResult | Exception) -> DocumentIntelligenceExtractor:
    extractor = DocumentIntelligenceExtractor(
        endpoint="https://fake.cognitiveservices.azure.com", credential=FakeCredential()
    )
    extractor._client.begin_analyze_document = lambda *args, **kwargs: FakePoller(outcome)  # type: ignore[method-assign]
    return extractor


def string_field(value: str, confidence: float = 0.95, page: int = 1) -> DocumentField:
    return DocumentField(
        type="string",
        value_string=value,
        content=value,
        confidence=confidence,
        bounding_regions=[
            BoundingRegion(page_number=page, polygon=[0.1, 0.1, 0.2, 0.1, 0.2, 0.2, 0.1, 0.2])
        ],
    )


def currency_field(
    amount: float, currency_code: str = "INR", confidence: float = 0.95
) -> DocumentField:
    return DocumentField(
        type="currency",
        value_currency=CurrencyValue(amount=amount, currency_code=currency_code),
        confidence=confidence,
    )


def number_field(value: float, confidence: float = 0.9) -> DocumentField:
    return DocumentField(type="number", value_number=value, confidence=confidence)


def date_field(value: date, confidence: float = 0.95) -> DocumentField:
    return DocumentField(type="date", value_date=value, confidence=confidence)


def address_field(text: str, confidence: float = 0.9) -> DocumentField:
    return DocumentField(
        type="address",
        value_address=AddressValue(street_address=text),
        content=text,
        confidence=confidence,
    )


def make_request(document_id: str = "doc-1") -> ExtractionRequest:
    return ExtractionRequest(
        document_id=document_id,
        blob_uri="blob://raw/doc-1.pdf",
        content_hash="hash-1",
        page_count=1,
    )


# --------------------------------------------------------------------------------------
# classify()
# --------------------------------------------------------------------------------------
def test_classify_maps_known_doc_type_to_invoice():
    doc = AnalyzedDocument(doc_type="invoice", confidence=0.93, spans=[], fields={})
    result = AnalyzeResult(
        api_version="2024-11-30",
        model_id="invoice-iq-classifier",
        content="",
        pages=[],
        documents=[doc],
    )
    extractor = make_extractor(result)

    classification = extractor.classify(make_request())

    assert classification == ClassificationResult(
        document_type=DocumentType.INVOICE, confidence=0.93
    )


def test_classify_maps_credit_note():
    doc = AnalyzedDocument(doc_type="creditNote", confidence=0.88, spans=[], fields={})
    result = AnalyzeResult(
        api_version="2024-11-30",
        model_id="invoice-iq-classifier",
        content="",
        pages=[],
        documents=[doc],
    )
    extractor = make_extractor(result)

    classification = extractor.classify(make_request())

    assert classification.document_type == DocumentType.CREDIT_NOTE


def test_classify_unknown_label_maps_to_other():
    doc = AnalyzedDocument(doc_type="statement", confidence=0.7, spans=[], fields={})
    result = AnalyzeResult(
        api_version="2024-11-30",
        model_id="invoice-iq-classifier",
        content="",
        pages=[],
        documents=[doc],
    )
    extractor = make_extractor(result)

    classification = extractor.classify(make_request())

    assert classification.document_type == DocumentType.OTHER


def test_classify_no_documents_returns_other_zero_confidence():
    result = AnalyzeResult(
        api_version="2024-11-30",
        model_id="invoice-iq-classifier",
        content="",
        pages=[],
        documents=[],
    )
    extractor = make_extractor(result)

    classification = extractor.classify(make_request())

    assert classification == ClassificationResult(document_type=DocumentType.OTHER, confidence=0.0)


# --------------------------------------------------------------------------------------
# extract() — scalar field mapping
# --------------------------------------------------------------------------------------
def test_extract_maps_scalar_fields_with_confidence_and_bounding_region():
    fields = {
        "VendorName": string_field("Acme Supplies Pvt Ltd", confidence=0.97, page=1),
        "InvoiceId": string_field("INV-1001", confidence=0.99),
        "InvoiceTotal": currency_field(1180.00, "INR", confidence=0.96),
    }
    doc = AnalyzedDocument(doc_type="invoice", confidence=0.98, spans=[], fields=fields)
    result = AnalyzeResult(
        api_version="2024-11-30", model_id="prebuilt-invoice", content="", pages=[], documents=[doc]
    )
    extractor = make_extractor(result)

    extraction = extractor.extract(make_request("doc-1"))

    invoice = extraction.invoice
    assert invoice.document_id == "doc-1"
    assert invoice.vendor_name == "Acme Supplies Pvt Ltd"
    assert invoice.invoice_number == "INV-1001"
    assert invoice.total == Decimal("1180.0")
    assert invoice.fields["vendor_name"].confidence == 0.97
    assert invoice.fields["vendor_name"].page == 1
    assert invoice.fields["vendor_name"].region is not None
    assert invoice.fields["vendor_name"].source == FieldSource.MODEL
    assert extraction.model_id == "prebuilt-invoice"
    assert extraction.model_version == "prebuilt-invoice"
    assert extraction.raw_response  # FR-024: raw response retained


def test_extract_analyses_raw_bytes_directly_when_request_carries_content():
    # A direct upload (no blob storage in front of it yet) sets `content` instead of a
    # real `blob_uri` — the adapter must send those bytes to Document Intelligence
    # directly rather than trying to dereference the (placeholder) blob_uri as a URL.
    doc = AnalyzedDocument(doc_type="invoice", confidence=0.98, spans=[], fields={})
    result = AnalyzeResult(
        api_version="2024-11-30", model_id="prebuilt-invoice", content="", pages=[], documents=[doc]
    )
    extractor = DocumentIntelligenceExtractor(
        endpoint="https://fake.cognitiveservices.azure.com", credential=FakeCredential()
    )
    captured: dict[str, object] = {}

    def fake_begin_analyze_document(model_id, body, **kwargs):  # type: ignore[no-untyped-def]
        captured["model_id"] = model_id
        captured["body"] = body
        return FakePoller(result)

    extractor._client.begin_analyze_document = fake_begin_analyze_document  # type: ignore[method-assign]

    request = ExtractionRequest(
        document_id="doc-1",
        blob_uri="local-upload://placeholder",
        content_hash="hash-1",
        page_count=1,
        content=b"%PDF-1.4 fake bytes",
    )
    extractor.extract(request)

    assert captured["body"].read() == b"%PDF-1.4 fake bytes"  # type: ignore[union-attr]


def test_extract_infers_currency_from_invoice_total():
    fields = {"InvoiceTotal": currency_field(500.00, "USD")}
    doc = AnalyzedDocument(doc_type="invoice", confidence=0.9, spans=[], fields=fields)
    result = AnalyzeResult(
        api_version="2024-11-30", model_id="prebuilt-invoice", content="", pages=[], documents=[doc]
    )
    extractor = make_extractor(result)

    extraction = extractor.extract(make_request())

    assert extraction.invoice.currency == "USD"


def test_extract_falls_back_to_amount_due_for_currency_when_total_absent():
    fields = {"AmountDue": currency_field(250.00, "GBP")}
    doc = AnalyzedDocument(doc_type="invoice", confidence=0.9, spans=[], fields=fields)
    result = AnalyzeResult(
        api_version="2024-11-30", model_id="prebuilt-invoice", content="", pages=[], documents=[doc]
    )
    extractor = make_extractor(result)

    extraction = extractor.extract(make_request())

    assert extraction.invoice.currency == "GBP"


def test_extract_with_no_currency_typed_fields_has_no_currency():
    fields = {"VendorName": string_field("Acme")}
    doc = AnalyzedDocument(doc_type="invoice", confidence=0.9, spans=[], fields=fields)
    result = AnalyzeResult(
        api_version="2024-11-30", model_id="prebuilt-invoice", content="", pages=[], documents=[doc]
    )
    extractor = make_extractor(result)

    extraction = extractor.extract(make_request())

    assert extraction.invoice.currency is None


def test_extract_maps_date_typed_fields_to_python_dates():
    # BR-06/07/08 all operate on invoice_date/due_date as `date` objects — if these came
    # through as strings instead, every date rule would silently misbehave.
    fields = {
        "InvoiceDate": date_field(date(2026, 8, 1)),
        "DueDate": date_field(date(2026, 8, 31)),
    }
    doc = AnalyzedDocument(doc_type="invoice", confidence=0.9, spans=[], fields=fields)
    result = AnalyzeResult(
        api_version="2024-11-30", model_id="prebuilt-invoice", content="", pages=[], documents=[doc]
    )
    extractor = make_extractor(result)

    extraction = extractor.extract(make_request())

    assert extraction.invoice.invoice_date == date(2026, 8, 1)
    assert extraction.invoice.due_date == date(2026, 8, 31)
    assert isinstance(extraction.invoice.invoice_date, date)


def test_extract_maps_address_field_as_its_ocrd_text_not_the_structured_value():
    fields = {"VendorAddress": address_field("123 Main St, Pune, India")}
    doc = AnalyzedDocument(doc_type="invoice", confidence=0.9, spans=[], fields=fields)
    result = AnalyzeResult(
        api_version="2024-11-30", model_id="prebuilt-invoice", content="", pages=[], documents=[doc]
    )
    extractor = make_extractor(result)

    extraction = extractor.extract(make_request())

    assert extraction.invoice.value("vendor_address") == "123 Main St, Pune, India"


def test_extract_missing_optional_field_is_absent_not_a_default_value():
    fields = {"VendorName": string_field("Acme")}
    doc = AnalyzedDocument(doc_type="invoice", confidence=0.9, spans=[], fields=fields)
    result = AnalyzeResult(
        api_version="2024-11-30", model_id="prebuilt-invoice", content="", pages=[], documents=[doc]
    )
    extractor = make_extractor(result)

    extraction = extractor.extract(make_request())

    assert "due_date" not in extraction.invoice.fields
    assert extraction.invoice.due_date is None


# --------------------------------------------------------------------------------------
# extract() — line items
# --------------------------------------------------------------------------------------
def test_extract_maps_line_items():
    item_1 = DocumentField(
        type="object",
        confidence=0.9,
        value_object={
            "Description": string_field("Widget A", confidence=0.9),
            "Quantity": number_field(10, confidence=0.95),
            "UnitPrice": currency_field(50.0, confidence=0.92),
            "Amount": currency_field(500.0, confidence=0.93),
            "ProductCode": string_field("SKU-1", confidence=0.85),
        },
    )
    item_2 = DocumentField(
        type="object",
        confidence=0.88,
        value_object={
            "Description": string_field("Widget B", confidence=0.6),  # lowest -> row confidence
            "Quantity": number_field(2),
            "UnitPrice": currency_field(20.0),
            "Amount": currency_field(40.0),
        },
    )
    fields = {"Items": DocumentField(type="array", confidence=0.9, value_array=[item_1, item_2])}
    doc = AnalyzedDocument(doc_type="invoice", confidence=0.9, spans=[], fields=fields)
    result = AnalyzeResult(
        api_version="2024-11-30", model_id="prebuilt-invoice", content="", pages=[], documents=[doc]
    )
    extractor = make_extractor(result)

    extraction = extractor.extract(make_request())

    lines = extraction.invoice.lines
    assert len(lines) == 2
    assert lines[0].line_number == 1
    assert lines[0].description == "Widget A"
    assert lines[0].quantity == Decimal("10")
    assert lines[0].unit_price == Decimal("50.0")
    assert lines[0].amount == Decimal("500.0")
    assert lines[0].product_code == "SKU-1"
    assert lines[1].line_number == 2
    assert lines[1].row_confidence == pytest.approx(
        0.6
    )  # the minimum of that line's field confidences
    assert lines[1].product_code is None


def test_extract_with_no_items_field_has_empty_lines():
    fields = {"VendorName": string_field("Acme")}
    doc = AnalyzedDocument(doc_type="invoice", confidence=0.9, spans=[], fields=fields)
    result = AnalyzeResult(
        api_version="2024-11-30", model_id="prebuilt-invoice", content="", pages=[], documents=[doc]
    )
    extractor = make_extractor(result)

    extraction = extractor.extract(make_request())

    assert extraction.invoice.lines == []


# --------------------------------------------------------------------------------------
# extract() — remittance bank account (BR-13's field)
# --------------------------------------------------------------------------------------
def test_extract_maps_remittance_bank_account_from_payment_details():
    entry = DocumentField(
        type="object",
        confidence=0.9,
        value_object={"BankAccountNumber": string_field("000111222333", confidence=0.9)},
    )
    fields = {"PaymentDetails": DocumentField(type="array", confidence=0.9, value_array=[entry])}
    doc = AnalyzedDocument(doc_type="invoice", confidence=0.9, spans=[], fields=fields)
    result = AnalyzeResult(
        api_version="2024-11-30", model_id="prebuilt-invoice", content="", pages=[], documents=[doc]
    )
    extractor = make_extractor(result)

    extraction = extractor.extract(make_request())

    assert extraction.invoice.value("remittance_bank_account") == "000111222333"


def test_extract_falls_back_to_iban_when_no_bank_account_number():
    entry = DocumentField(
        type="object", confidence=0.9, value_object={"IBAN": string_field("GB29NWBK60161331926819")}
    )
    fields = {"PaymentDetails": DocumentField(type="array", confidence=0.9, value_array=[entry])}
    doc = AnalyzedDocument(doc_type="invoice", confidence=0.9, spans=[], fields=fields)
    result = AnalyzeResult(
        api_version="2024-11-30", model_id="prebuilt-invoice", content="", pages=[], documents=[doc]
    )
    extractor = make_extractor(result)

    extraction = extractor.extract(make_request())

    assert extraction.invoice.value("remittance_bank_account") == "GB29NWBK60161331926819"


# --------------------------------------------------------------------------------------
# extract() — no documents
# --------------------------------------------------------------------------------------
def test_extract_with_no_documents_raises_permanent_error():
    result = AnalyzeResult(
        api_version="2024-11-30", model_id="prebuilt-invoice", content="", pages=[], documents=[]
    )
    extractor = make_extractor(result)

    with pytest.raises(PermanentExtractionError):
        extractor.extract(make_request())


# --------------------------------------------------------------------------------------
# Error translation (Article XII: permanent vs transient, 429 is transient despite 4xx)
# --------------------------------------------------------------------------------------
def test_authentication_error_is_permanent():
    extractor = make_extractor(ClientAuthenticationError(message="no valid credential"))
    with pytest.raises(PermanentExtractionError):
        extractor.extract(make_request())


def test_400_is_permanent():
    err = HttpResponseError(message="bad request")
    err.status_code = 400
    extractor = make_extractor(err)
    with pytest.raises(PermanentExtractionError):
        extractor.extract(make_request())


def test_404_is_permanent():
    err = HttpResponseError(message="model not found")
    err.status_code = 404
    extractor = make_extractor(err)
    with pytest.raises(PermanentExtractionError):
        extractor.extract(make_request())


def test_429_throttling_is_transient_not_permanent():
    # The important edge case: 429 is a 4xx but must never be treated as permanent.
    err = HttpResponseError(message="too many requests")
    err.status_code = 429
    extractor = make_extractor(err)
    with pytest.raises(TransientExtractionError):
        extractor.extract(make_request())


def test_500_is_transient():
    err = HttpResponseError(message="internal server error")
    err.status_code = 500
    extractor = make_extractor(err)
    with pytest.raises(TransientExtractionError):
        extractor.extract(make_request())


def test_service_request_error_is_transient():
    extractor = make_extractor(ServiceRequestError(message="connection reset"))
    with pytest.raises(TransientExtractionError):
        extractor.extract(make_request())
