"""Document Intelligence adapter — the one sanctioned exception to Article I's "no Azure
SDK in core" rule.

See `.specify/memory/constitution.md` Article I — its own enforcement grep deliberately
excludes this file — and `plan.md` §2, which names this exact file "the only file importing
the DI SDK". Every canonical field name produced here (FR-020) is what the rest of core
operates on from this point forward; nothing downstream of this file ever sees a Document
Intelligence SDK type. `pyproject.toml`'s import-linter contract enforces that every *other*
file in `core` still can't import `azure` — this one file is the deliberate, narrow hole.

Auth is `TokenCredential` only, never a key (constitution Article III) — the Bicep for this
resource sets `disableLocalAuth: true`, so a key wouldn't even authenticate.
"""

from __future__ import annotations

import io
from decimal import Decimal
from typing import Any

from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import (
    AnalyzedDocument,
    AnalyzeDocumentRequest,
    AnalyzeResult,
    DocumentField,
)
from azure.core.credentials import TokenCredential
from azure.core.exceptions import (
    ClientAuthenticationError,
    HttpResponseError,
    ServiceRequestError,
    ServiceResponseError,
)

from core.extraction.base import (
    ClassificationResult,
    DocumentExtractor,
    DocumentType,
    ExtractionError,
    ExtractionRequest,
    ExtractionResult,
    PermanentExtractionError,
    TransientExtractionError,
)
from core.models import BoundingRegion, ExtractedField, FieldSource, Invoice, LineItem

DEFAULT_INVOICE_MODEL_ID = "prebuilt-invoice"

# The customer trains their own classifier (plan.md §1: "custom classification model") —
# this model ID and label map are reasonable defaults, not verified against a real trained
# model, since no such model exists yet (blocked behind Phase 2/T-042 in practice).
# `DocumentIntelligenceExtractor.__init__` accepts overrides for exactly this reason.
DEFAULT_CLASSIFIER_MODEL_ID = "invoice-iq-classifier"
DEFAULT_CLASSIFIER_TYPE_MAP: dict[str, DocumentType] = {
    "invoice": DocumentType.INVOICE,
    "creditNote": DocumentType.CREDIT_NOTE,
}

# FR-020: canonical field name -> prebuilt-invoice's top-level field name. This is the
# stable, documented schema for Document Intelligence v4.0's prebuilt-invoice model.
_SCALAR_FIELD_MAP: dict[str, str] = {
    "vendor_name": "VendorName",
    "vendor_address": "VendorAddress",
    "vendor_tax_id": "VendorTaxId",
    "customer_name": "CustomerName",
    "invoice_number": "InvoiceId",
    "invoice_date": "InvoiceDate",
    "due_date": "DueDate",
    "po_number": "PurchaseOrder",
    "subtotal": "SubTotal",
    "tax_total": "TotalTax",
    "total": "InvoiceTotal",
    "amount_due": "AmountDue",
    "payment_terms": "PaymentTerm",
}

# Currency isn't its own top-level field — it rides along on whichever currency-typed
# field carries it. Checked in this order; first one present wins.
_CURRENCY_SOURCE_FIELDS = ("InvoiceTotal", "AmountDue", "SubTotal", "TotalTax")

# FR-021: line item fields DI reliably provides. Deliberately does not include a per-line
# tax rate — prebuilt-invoice exposes a line-level "Tax" *amount* field, not a rate, and
# guessing at an undocumented rate field name here would be worse than leaving it unmapped.
_LINE_ITEM_FIELD_MAP = {
    "description": "Description",
    "quantity": "Quantity",
    "unit_price": "UnitPrice",
    "amount": "Amount",
    "product_code": "ProductCode",
}


class DocumentIntelligenceExtractor(DocumentExtractor):
    def __init__(
        self,
        endpoint: str,
        credential: TokenCredential,
        *,
        invoice_model_id: str = DEFAULT_INVOICE_MODEL_ID,
        classifier_model_id: str = DEFAULT_CLASSIFIER_MODEL_ID,
        classifier_type_map: dict[str, DocumentType] | None = None,
    ) -> None:
        self._client = DocumentIntelligenceClient(endpoint=endpoint, credential=credential)
        self._invoice_model_id = invoice_model_id
        self._classifier_model_id = classifier_model_id
        self._classifier_type_map = classifier_type_map or dict(DEFAULT_CLASSIFIER_TYPE_MAP)

    def classify(self, request: ExtractionRequest) -> ClassificationResult:
        # NFR-042 / plan.md §4: classification is restricted to the first page — it's
        # billed per page, and nothing about "is this an invoice" needs page 2 onward.
        result = self._analyze(self._classifier_model_id, request, pages="1")
        if not result.documents:
            return ClassificationResult(document_type=DocumentType.OTHER, confidence=0.0)
        doc = result.documents[0]
        document_type = self._classifier_type_map.get(doc.doc_type, DocumentType.OTHER)
        return ClassificationResult(document_type=document_type, confidence=doc.confidence)

    def extract(self, request: ExtractionRequest) -> ExtractionResult:
        model_id = request.model_hint or self._invoice_model_id
        result = self._analyze(model_id, request)
        if not result.documents:
            raise PermanentExtractionError(
                "Document Intelligence returned no documents for this request"
            )
        invoice = _map_to_invoice(request.document_id, result.documents[0])
        return ExtractionResult(
            invoice=invoice,
            model_id=model_id,
            model_version=result.model_id,
            raw_response=result.as_dict(),  # FR-024: kept verbatim, reprocess without re-billing
        )

    def _analyze(self, model_id: str, request: ExtractionRequest, **kwargs: Any) -> AnalyzeResult:
        body: AnalyzeDocumentRequest | io.BytesIO = (
            io.BytesIO(request.content)
            if request.content is not None
            else AnalyzeDocumentRequest(url_source=request.blob_uri)
        )
        try:
            poller = self._client.begin_analyze_document(model_id, body, **kwargs)
            return poller.result()
        except ClientAuthenticationError as exc:
            raise PermanentExtractionError(f"authentication failed: {exc}") from exc
        except HttpResponseError as exc:
            raise _translate_http_error(exc) from exc
        except (ServiceRequestError, ServiceResponseError) as exc:
            raise TransientExtractionError(
                f"transient failure calling Document Intelligence: {exc}"
            ) from exc


def _translate_http_error(exc: HttpResponseError) -> ExtractionError:
    status_code = exc.status_code
    if status_code == 429:
        # Throttling is transient by definition (plan.md §4: "trigger exponential
        # backoff, never a tight retry loop") — it must never be classified as permanent
        # just because 429 falls in the 4xx range.
        return TransientExtractionError(
            f"Document Intelligence throttled the request: {exc.message}"
        )
    if status_code is not None and 400 <= status_code < 500:
        # Article XII: a billed call is never retried on a permanent error.
        return PermanentExtractionError(
            f"Document Intelligence rejected the request ({status_code}): {exc.message}"
        )
    return TransientExtractionError(
        f"Document Intelligence request failed ({status_code}): {exc.message}"
    )


def _field_value(field: DocumentField) -> Any:
    if field.value_currency is not None:
        return Decimal(str(field.value_currency.amount))
    if field.value_date is not None:
        return field.value_date
    if field.value_address is not None:
        # Kept as the OCR'd text rather than the structured AddressValue — a reviewer
        # verifies an address as one string, not field-by-field.
        return field.content
    if field.value_string is not None:
        return field.value_string
    if field.value_number is not None:
        return Decimal(str(field.value_number))
    if field.value_integer is not None:
        return field.value_integer
    if field.value_phone_number is not None:
        return field.value_phone_number
    if field.value_country_region is not None:
        return field.value_country_region
    return field.content


def _bounding_region(field: DocumentField) -> BoundingRegion | None:
    if not field.bounding_regions:
        return None
    region = field.bounding_regions[0]
    return BoundingRegion(page=region.page_number, polygon=tuple(region.polygon))


def _to_extracted_field(name: str, field: DocumentField | None) -> ExtractedField | None:
    if field is None:
        return None
    region = _bounding_region(field)
    return ExtractedField(
        name=name,
        value=_field_value(field),
        confidence=field.confidence or 0.0,
        page=region.page if region else None,
        region=region,
        source=FieldSource.MODEL,
    )


def _infer_currency(raw_fields: dict[str, DocumentField]) -> ExtractedField | None:
    for di_name in _CURRENCY_SOURCE_FIELDS:
        field = raw_fields.get(di_name)
        if (
            field is not None
            and field.value_currency is not None
            and field.value_currency.currency_code
        ):
            region = _bounding_region(field)
            return ExtractedField(
                name="currency",
                value=field.value_currency.currency_code,
                confidence=field.confidence or 0.0,
                page=region.page if region else None,
                region=region,
                source=FieldSource.MODEL,
            )
    return None


def _map_remittance_bank_account(raw_fields: dict[str, DocumentField]) -> ExtractedField | None:
    payment_details = raw_fields.get("PaymentDetails")
    if payment_details is None or not payment_details.value_array:
        return None
    first = payment_details.value_array[0]
    entry = first.value_object or {}
    account_field = entry.get("BankAccountNumber") or entry.get("IBAN")
    return _to_extracted_field("remittance_bank_account", account_field)


def _decimal_field(field: DocumentField | None, default: Decimal = Decimal("0")) -> Decimal:
    if field is None:
        return default
    value = _field_value(field)
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    return default


def _map_line_items(raw_fields: dict[str, DocumentField]) -> list[LineItem]:
    items_field = raw_fields.get("Items")
    if items_field is None or not items_field.value_array:
        return []
    lines: list[LineItem] = []
    for index, item_field in enumerate(items_field.value_array, start=1):
        entry = item_field.value_object or {}
        description_field = entry.get(_LINE_ITEM_FIELD_MAP["description"])
        description = _field_value(description_field) if description_field is not None else ""
        product_code_field = entry.get(_LINE_ITEM_FIELD_MAP["product_code"])
        product_code = _field_value(product_code_field) if product_code_field is not None else None
        confidences = [f.confidence for f in entry.values() if f.confidence is not None]
        row_confidence = min(confidences) if confidences else (item_field.confidence or 0.0)
        lines.append(
            LineItem(
                line_number=index,
                description=str(description) if description else "",
                quantity=_decimal_field(entry.get(_LINE_ITEM_FIELD_MAP["quantity"])),
                unit_price=_decimal_field(entry.get(_LINE_ITEM_FIELD_MAP["unit_price"])),
                amount=_decimal_field(entry.get(_LINE_ITEM_FIELD_MAP["amount"])),
                tax_rate=None,
                product_code=str(product_code) if product_code else None,
                row_confidence=row_confidence,
            )
        )
    return lines


def _map_to_invoice(document_id: str, doc: AnalyzedDocument) -> Invoice:
    raw_fields = doc.fields or {}
    fields: dict[str, ExtractedField] = {}

    for canonical_name, di_name in _SCALAR_FIELD_MAP.items():
        extracted = _to_extracted_field(canonical_name, raw_fields.get(di_name))
        if extracted is not None:
            fields[canonical_name] = extracted

    currency_field = _infer_currency(raw_fields)
    if currency_field is not None:
        fields["currency"] = currency_field

    remittance_field = _map_remittance_bank_account(raw_fields)
    if remittance_field is not None:
        fields["remittance_bank_account"] = remittance_field

    return Invoice(
        invoice_id=f"inv-{document_id}",
        document_id=document_id,
        fields=fields,
        lines=_map_line_items(raw_fields),
    )
