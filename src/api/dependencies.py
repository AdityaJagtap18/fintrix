"""Dependency injection wiring for the FastAPI app.

Three things here are explicit placeholders, not real implementations, and each is called
out at the point it's used so nobody mistakes it for finished work:

* `InMemoryReviewAssignmentRepository` — satisfies `ReviewAssignmentRepository`
  structurally so the API layer and its tests can run today, but state doesn't survive a
  restart and there's no real concurrency control. T-047 (SQLAlchemy/Azure SQL
  repositories) replaces this; nothing above `get_review_repo()` needs to change when it
  does, since routers depend on the Protocol, not this class.
* `get_current_reviewer` — reads an `X-Reviewer` header instead of validating an Entra ID
  bearer token. T-062 replaces this with real JWT validation (NFR-021/022). Every caller
  of this dependency needs revisiting before this serves anything but a laptop.
* `get_azure_credential` — falls back to a client-secret app registration
  (`ClientSecretCredential`) when neither `az login` nor Managed Identity is available.
  This is genuinely how the constitution's own auth story degrades (Article III: "federated
  OIDC locally via az login, Managed Identity in Azure") on a machine where the tenant's
  app-consent policy blocks even azure-cli's own client ID — see scripts/azure_login.py's
  docstring. `DefaultAzureCredential` is still tried first for anywhere that isn't this box.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Annotated

from azure.core.credentials import TokenCredential
from azure.identity import ClientSecretCredential, DefaultAzureCredential
from dotenv import load_dotenv
from fastapi import Header, HTTPException, status

from core.extraction.document_intelligence import DocumentIntelligenceExtractor
from core.models import ReviewAssignment
from core.persistence import ReviewAssignmentRepository

load_dotenv()


def now_utc() -> datetime:
    """This system's datetime convention: naive, but always UTC. Mixing this with a
    timezone-aware value anywhere will raise on comparison — that's deliberate, it's
    cheaper to catch here than to debug a silent timezone bug in `is_expired`."""
    return datetime.now(UTC).replace(tzinfo=None)


class InMemoryReviewAssignmentRepository:
    """Dev/test placeholder for `ReviewAssignmentRepository` — see module docstring."""

    def __init__(self) -> None:
        self._store: dict[str, ReviewAssignment] = {}

    def get(self, invoice_id: str) -> ReviewAssignment | None:
        return self._store.get(invoice_id)

    def save(self, assignment: ReviewAssignment) -> None:
        self._store[assignment.invoice_id] = assignment

    def find_expired(self, now: datetime) -> list[ReviewAssignment]:
        return [a for a in self._store.values() if a.is_expired(now)]


_review_repo = InMemoryReviewAssignmentRepository()


def get_review_repo() -> ReviewAssignmentRepository:
    return _review_repo


def get_current_reviewer(x_reviewer: Annotated[str | None, Header()] = None) -> str:
    if not x_reviewer:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-Reviewer header required (dev auth placeholder pending T-062)",
        )
    return x_reviewer


def get_azure_credential() -> TokenCredential:
    client_id = os.environ.get("AZURE_CLIENT_ID")
    client_secret = os.environ.get("AZURE_CLIENT_SECRET")
    tenant_id = os.environ.get("AZURE_TENANT_ID")
    if client_id and client_secret and tenant_id:
        return ClientSecretCredential(
            tenant_id=tenant_id, client_id=client_id, client_secret=client_secret
        )
    # Tries az CLI's cached login, then Managed Identity — the real path everywhere except
    # this one dev box (see module docstring).
    return DefaultAzureCredential()


class _DocumentIntelligenceProvider:
    """Built once per process, not once per request — constructing the client is cheap,
    but there's no reason to re-read env vars and re-authenticate on every call."""

    def __init__(self) -> None:
        self._extractor: DocumentIntelligenceExtractor | None = None

    def get(self) -> DocumentIntelligenceExtractor:
        if self._extractor is None:
            endpoint = os.environ.get("DOCUMENT_INTELLIGENCE_ENDPOINT")
            if not endpoint:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="DOCUMENT_INTELLIGENCE_ENDPOINT is not set — see .env.example",
                )
            self._extractor = DocumentIntelligenceExtractor(
                endpoint=endpoint, credential=get_azure_credential()
            )
        return self._extractor


_document_intelligence_provider = _DocumentIntelligenceProvider()


def get_document_intelligence_extractor() -> DocumentIntelligenceExtractor:
    return _document_intelligence_provider.get()
