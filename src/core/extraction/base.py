"""The extraction provider interface (constitution Article II). Every engine — Document
Intelligence today, anything else tomorrow — implements `DocumentExtractor`. Nothing
downstream of this file ever imports an engine SDK directly (NFR-050): swapping engines is
one new adapter class, not a rewrite.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from core.models import Invoice


class DocumentType(StrEnum):
    INVOICE = "invoice"
    CREDIT_NOTE = "credit_note"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class ExtractionRequest:
    document_id: str
    blob_uri: str
    content_hash: str
    page_count: int
    model_hint: str | None = None  # vendor-specific model override, if configured (FR-012)


@dataclass(frozen=True, slots=True)
class ClassificationResult:
    document_type: DocumentType
    confidence: float


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    invoice: Invoice
    model_id: str
    model_version: str
    # Retained verbatim so validation/routing changes never re-invoke a billed call (FR-024).
    raw_response: dict[str, Any]


class ExtractionError(Exception):
    """Base for every error an extractor can raise."""


class TransientExtractionError(ExtractionError):
    """Retry with backoff — throttling, timeout, transient service unavailability."""


class PermanentExtractionError(ExtractionError):
    """Never retry (constitution Article XII: a billed call is never retried on a
    permanent error) — malformed input, unsupported file type, a 4xx from the engine."""


class DocumentExtractor(ABC):
    """One engine, two responsibilities: tell us what a document is, then read it."""

    @abstractmethod
    def classify(self, request: ExtractionRequest) -> ClassificationResult:
        """FR-010: classify before extraction is attempted."""

    @abstractmethod
    def extract(self, request: ExtractionRequest) -> ExtractionResult:
        """FR-020..024: full field and line extraction with confidence and bounding
        regions, keeping the raw engine response for reprocessing without re-billing."""
