"""FR-010: classify before extraction; quarantine anything that isn't confidently an
invoice or credit note (AC-11.1). The decision itself is pure — the actual quarantine
write and sender notification are infrastructure concerns wired up in Phase 2's
orchestrator activities.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from core.extraction.base import ClassificationResult, DocumentType

DEFAULT_MIN_CLASSIFICATION_CONFIDENCE = 0.5


class DocumentDisposition(StrEnum):
    PROCEED = "proceed"
    QUARANTINE = "quarantine"


@dataclass(frozen=True, slots=True)
class ClassificationOutcome:
    disposition: DocumentDisposition
    reason: str | None = None


def decide_disposition(
    result: ClassificationResult,
    *,
    min_confidence: float = DEFAULT_MIN_CLASSIFICATION_CONFIDENCE,
) -> ClassificationOutcome:
    """AC-11.1: a document not classified as an invoice or credit note is quarantined with
    the detected type as the reason. A low-confidence classification of *any* type is
    treated the same way — better to quarantine for a human to look at than guess."""
    if result.document_type not in (DocumentType.INVOICE, DocumentType.CREDIT_NOTE):
        return ClassificationOutcome(
            disposition=DocumentDisposition.QUARANTINE,
            reason=f"detected_type:{result.document_type.value}",
        )
    if result.confidence < min_confidence:
        return ClassificationOutcome(
            disposition=DocumentDisposition.QUARANTINE,
            reason="classification_confidence_too_low",
        )
    return ClassificationOutcome(disposition=DocumentDisposition.PROCEED)
