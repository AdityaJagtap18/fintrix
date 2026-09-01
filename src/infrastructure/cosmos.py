"""Cosmos DB client construction. Authentication follows Article III: `TokenCredential`
only (`DefaultAzureCredential` locally, Managed Identity in Azure) — never a connection
string or key. Reuses whatever credential `get_azure_credential()` in `api/dependencies.py`
already produced; this module doesn't open a second auth path.
"""

from __future__ import annotations

from azure.core.credentials import TokenCredential
from azure.cosmos import CosmosClient


def build_client(endpoint: str, credential: TokenCredential) -> CosmosClient:
    return CosmosClient(endpoint, credential=credential)
