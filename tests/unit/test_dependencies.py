"""Unit tests for the credential/extractor plumbing in api/dependencies.py. No live Azure
call is ever made here — ClientSecretCredential and DefaultAzureCredential only build a
credential object; they don't authenticate until something actually calls `get_token`.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from azure.identity import ClientSecretCredential, DefaultAzureCredential
from fastapi import HTTPException

import api.dependencies as dependencies
from api.dependencies import (
    InMemoryReviewAssignmentRepository,
    _CosmosRepositoryProvider,
    _DocumentIntelligenceProvider,
    get_azure_credential,
    get_review_repo,
)
from infrastructure.repositories.review_assignments import CosmosReviewAssignmentRepository
from tests.unit.fakes import FakeContainer, FakeCosmosClient, FakeDatabase

_ALL_CONTAINERS = (
    "vendors",
    "vendor_tax_ids",
    "documents",
    "invoices",
    "purchase_orders",
    "review_assignments",
    "audit_log",
)


def _fake_build_client(*_args: object, **_kwargs: object) -> FakeCosmosClient:
    containers = {name: FakeContainer(partition_key_path="/id") for name in _ALL_CONTAINERS}
    return FakeCosmosClient(database=FakeDatabase(containers=containers))


@pytest.fixture
def clean_azure_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for var in ("AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET", "AZURE_TENANT_ID"):
        monkeypatch.delenv(var, raising=False)
    yield


def test_uses_client_secret_credential_when_app_registration_is_fully_configured(
    clean_azure_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AZURE_CLIENT_ID", "client-id")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("AZURE_TENANT_ID", "tenant-id")

    credential = get_azure_credential()

    assert isinstance(credential, ClientSecretCredential)


@pytest.mark.parametrize(
    "missing_var", ["AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET", "AZURE_TENANT_ID"]
)
def test_falls_back_to_default_credential_when_any_piece_is_missing(
    clean_azure_env: None, monkeypatch: pytest.MonkeyPatch, missing_var: str
) -> None:
    values = {
        "AZURE_CLIENT_ID": "client-id",
        "AZURE_CLIENT_SECRET": "client-secret",
        "AZURE_TENANT_ID": "tenant-id",
    }
    del values[missing_var]
    for key, value in values.items():
        monkeypatch.setenv(key, value)

    credential = get_azure_credential()

    assert isinstance(credential, DefaultAzureCredential)


def test_extractor_provider_raises_503_when_endpoint_is_not_configured(
    clean_azure_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DOCUMENT_INTELLIGENCE_ENDPOINT", raising=False)
    provider = _DocumentIntelligenceProvider()

    with pytest.raises(HTTPException) as exc_info:
        provider.get()

    assert exc_info.value.status_code == 503


def test_extractor_provider_builds_once_and_reuses_the_same_instance(
    clean_azure_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOCUMENT_INTELLIGENCE_ENDPOINT", "https://fake.cognitiveservices.azure.com")
    provider = _DocumentIntelligenceProvider()

    first = provider.get()
    second = provider.get()

    assert first is second


def test_cosmos_provider_raises_503_when_endpoint_is_not_configured(
    clean_azure_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("COSMOS_ENDPOINT", raising=False)
    provider = _CosmosRepositoryProvider()

    with pytest.raises(HTTPException) as exc_info:
        provider.vendors()

    assert exc_info.value.status_code == 503


def test_cosmos_provider_builds_the_database_client_once_and_reuses_it(
    clean_azure_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COSMOS_ENDPOINT", "https://fake.documents.azure.com")
    monkeypatch.setenv("COSMOS_DATABASE", "db-fake")
    monkeypatch.setattr(dependencies, "build_client", _fake_build_client)
    provider = _CosmosRepositoryProvider()

    provider.vendors()
    provider.invoices()

    assert provider._database is not None  # one client built, shared by every repo


def test_get_review_repo_falls_back_to_in_memory_when_cosmos_not_configured(
    clean_azure_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("COSMOS_ENDPOINT", raising=False)
    monkeypatch.setattr(dependencies, "_cosmos_provider", _CosmosRepositoryProvider())

    assert isinstance(get_review_repo(), InMemoryReviewAssignmentRepository)


def test_get_review_repo_uses_cosmos_when_configured(
    clean_azure_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COSMOS_ENDPOINT", "https://fake.documents.azure.com")
    monkeypatch.setenv("COSMOS_DATABASE", "db-fake")
    monkeypatch.setattr(dependencies, "build_client", _fake_build_client)
    monkeypatch.setattr(dependencies, "_cosmos_provider", _CosmosRepositoryProvider())

    assert isinstance(get_review_repo(), CosmosReviewAssignmentRepository)
