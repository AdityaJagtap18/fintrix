from decimal import Decimal

import pytest

from core.extraction.base import (
    ClassificationResult,
    DocumentExtractor,
    DocumentType,
    ExtractionRequest,
    ExtractionResult,
    PermanentExtractionError,
    TransientExtractionError,
)
from core.models import ExtractedField, Invoice


class FakeExtractor(DocumentExtractor):
    """A minimal, spec-compliant implementation used only to prove the ABC is usable —
    stands in for the real Document Intelligence adapter (T-041, Phase 2 Azure work)."""

    def classify(self, request: ExtractionRequest) -> ClassificationResult:
        return ClassificationResult(document_type=DocumentType.INVOICE, confidence=0.99)

    def extract(self, request: ExtractionRequest) -> ExtractionResult:
        invoice = Invoice(
            invoice_id=f"inv-{request.document_id}",
            document_id=request.document_id,
            fields={"total": ExtractedField(name="total", value=Decimal("100.00"), confidence=0.9)},
        )
        return ExtractionResult(
            invoice=invoice, model_id="fake-model", model_version="1.0", raw_response={"ok": True}
        )


def test_extractor_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        DocumentExtractor()  # type: ignore[abstract]


def test_fake_extractor_round_trip():
    extractor = FakeExtractor()
    request = ExtractionRequest(
        document_id="doc-1", blob_uri="blob://raw/doc-1.pdf", content_hash="abc123", page_count=2
    )
    classification = extractor.classify(request)
    assert classification.document_type == DocumentType.INVOICE

    result = extractor.extract(request)
    assert result.invoice.document_id == "doc-1"
    assert result.raw_response == {"ok": True}


def test_transient_and_permanent_errors_are_distinct_and_both_extraction_errors():
    assert issubclass(TransientExtractionError, Exception)
    assert issubclass(PermanentExtractionError, Exception)
    assert not issubclass(TransientExtractionError, PermanentExtractionError)
    assert not issubclass(PermanentExtractionError, TransientExtractionError)


def test_extraction_request_is_immutable():
    request = ExtractionRequest(document_id="d", blob_uri="b", content_hash="h", page_count=1)
    with pytest.raises(AttributeError):
        request.page_count = 5  # type: ignore[misc]
