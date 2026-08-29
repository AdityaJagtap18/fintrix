from core.extraction.base import ClassificationResult, DocumentType
from core.extraction.classification import DocumentDisposition, decide_disposition


def test_invoice_above_threshold_proceeds():
    result = ClassificationResult(document_type=DocumentType.INVOICE, confidence=0.95)
    outcome = decide_disposition(result)
    assert outcome.disposition == DocumentDisposition.PROCEED
    assert outcome.reason is None


def test_credit_note_above_threshold_proceeds():
    result = ClassificationResult(document_type=DocumentType.CREDIT_NOTE, confidence=0.9)
    assert decide_disposition(result).disposition == DocumentDisposition.PROCEED


def test_non_invoice_is_quarantined_with_detected_type_as_reason():
    # AC-11.1: quarantined with the detected type as the reason.
    result = ClassificationResult(document_type=DocumentType.OTHER, confidence=0.99)
    outcome = decide_disposition(result)
    assert outcome.disposition == DocumentDisposition.QUARANTINE
    assert outcome.reason == "detected_type:other"


def test_low_confidence_invoice_is_quarantined():
    result = ClassificationResult(document_type=DocumentType.INVOICE, confidence=0.2)
    outcome = decide_disposition(result, min_confidence=0.5)
    assert outcome.disposition == DocumentDisposition.QUARANTINE
    assert outcome.reason == "classification_confidence_too_low"


def test_threshold_is_configurable():
    result = ClassificationResult(document_type=DocumentType.INVOICE, confidence=0.6)
    assert decide_disposition(result, min_confidence=0.5).disposition == DocumentDisposition.PROCEED
    assert (
        decide_disposition(result, min_confidence=0.7).disposition == DocumentDisposition.QUARANTINE
    )
