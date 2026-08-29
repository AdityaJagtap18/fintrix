"""Unit tests for the credential/extractor plumbing in api/dependencies.py. No live Azure
call is ever made here — ClientSecretCredential and DefaultAzureCredential only build a
credential object; they don't authenticate until something actually calls `get_token`.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from azure.identity import ClientSecretCredential, DefaultAzureCredential
from fastapi import HTTPException

from api.dependencies import _DocumentIntelligenceProvider, get_azure_credential


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
