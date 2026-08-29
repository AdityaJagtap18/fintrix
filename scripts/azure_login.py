"""One-time interactive login for local Document Intelligence calls — no `az` CLI
required. Uses `azure-identity`'s device-code flow directly against Azure CLI's own
public, unauthenticated app registration (client ID below is not a secret; it's the same
ID `az login` itself uses under the hood, published by Microsoft), so there's no need to
register a custom AAD app just to test this locally.

The resulting token is cached to disk (via MSAL's cache, `~/.IdentityService` on Linux) so
this only needs to run once — `api/dependencies.py`'s credential lookup reuses the same
cache silently afterward, the same way `DefaultAzureCredential` would reuse a real `az
login` session if the CLI were installed.

Run with:
    .venv/bin/python scripts/azure_login.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from azure.identity import DeviceCodeCredential, TokenCachePersistenceOptions

# az login's own published client ID — public knowledge, safe to hardcode.
# https://github.com/Azure/azure-cli/blob/dev/src/azure-cli-core/azure/cli/core/_profile.py
AZURE_CLI_CLIENT_ID = "04b07795-8ddb-461a-bbee-02f9e1bff9d3"

CACHE_NAME = "invoice-iq-local-login"


def _load_tenant_id() -> str | None:
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.exists():
        return None
    for line in env_path.read_text().splitlines():
        if line.startswith("AZURE_TENANT_ID="):
            value = line.split("=", 1)[1].strip()
            return value or None
    return None


def get_credential() -> DeviceCodeCredential:
    """Returns a credential backed by the persistent cache — silent on every call after
    the first successful interactive login, since MSAL serves a cached refresh token
    without prompting again as long as it hasn't expired or been revoked."""
    return DeviceCodeCredential(
        client_id=AZURE_CLI_CLIENT_ID,
        tenant_id=_load_tenant_id() or "organizations",
        # This dev box has no OS keyring (headless, no libsecret) to encrypt the cache
        # with — same constraint api/dependencies.py's other placeholders operate under.
        # The cache is a refresh token, not a password; it lives at ~/.IdentityService
        # under the user's own file permissions. Acceptable for local dev, not for a
        # shared or production host.
        cache_persistence_options=TokenCachePersistenceOptions(
            name=CACHE_NAME, allow_unencrypted_storage=True
        ),
    )


def main() -> int:
    credential = get_credential()
    try:
        # Cognitive Services' default resource scope — Document Intelligence is a
        # Cognitive Services account, so this is the token real calls need.
        token = credential.get_token("https://cognitiveservices.azure.com/.default")
    except Exception as exc:  # noqa: BLE001 — this is a CLI entry point, not library code
        print(f"Login failed: {exc}", file=sys.stderr)
        return 1
    print(f"Login succeeded. Token expires at (unix): {token.expires_on}")
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    raise SystemExit(main())
