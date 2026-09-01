"""One-time provisioning for the Cosmos DB deployment described in
`infra/modules/identity.bicep` and `infra/modules/cosmos.bicep` — no `az` CLI involved
anywhere. Uses the same device-code pattern as `scripts/azure_login.py` (az CLI's own
published, public client ID — not a secret, just borrowing the identity az itself uses)
plus direct REST calls to Azure Resource Manager and Microsoft Graph. The Bicep files
still are the source of truth (Article IX) — this script compiles them with the standalone
`bicep` binary and deploys the resulting ARM JSON as-is; it does not reimplement the
infrastructure definition.

What this creates:
    * A resource group (rg-apauto-dev by default)
    * A user-assigned managed identity (free — used only as a param cosmos.bicep expects)
    * A Cosmos DB account in serverless mode (pay-per-request, no idle floor)
    * An Entra app registration + service principal + client secret for local dev auth,
      granted Cosmos data-plane RBAC only (no subscription-level role) — Article III

What it does NOT create: Document Intelligence, Storage, Service Bus, Container Apps, or
Functions. Nothing else in infra/ is touched.

At the end, `.env` in the repo root is updated directly — the client secret is written to
that local, gitignored file and never printed back to this terminal.

Run with:
    .venv/bin/python scripts/provision_cosmos.py
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from azure.identity import DeviceCodeCredential, TokenCachePersistenceOptions

# az login's own published client ID — public knowledge, safe to hardcode (see
# scripts/azure_login.py for the same usage and citation).
AZURE_CLI_CLIENT_ID = "04b07795-8ddb-461a-bbee-02f9e1bff9d3"
CACHE_NAME = "invoice-iq-local-login"

ARM_BASE = "https://management.azure.com"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"
ARM_API_VERSION = "2021-04-01"

BASE_NAME = "apauto"
ENV_NAME = "dev"
LOCATION = "centralindia"
RESOURCE_GROUP = f"rg-{BASE_NAME}-{ENV_NAME}"
TAGS = {"project": "invoice-iq", "environment": ENV_NAME, "managedBy": "bicep"}

REPO_ROOT = Path(__file__).parent.parent
ENV_PATH = REPO_ROOT / ".env"
BICEPPARAM_PATH = REPO_ROOT / "infra" / "main.dev.bicepparam"


def _find_bicep() -> str:
    found = shutil.which("bicep")
    if found:
        return found
    fallback = Path.home() / ".local" / "bin" / "bicep"
    if fallback.exists():
        return str(fallback)
    raise SystemExit(
        "Can't find the standalone bicep compiler. Install it (not az CLI — the "
        "standalone binary from https://github.com/Azure/bicep/releases) and make sure "
        "it's on PATH or at ~/.local/bin/bicep."
    )


def compile_bicep(bicep_path: Path) -> dict[str, Any]:
    bicep_exe = _find_bicep()
    out_path = bicep_path.with_suffix(".generated.json")
    subprocess.run([bicep_exe, "build", str(bicep_path), "--outfile", str(out_path)], check=True)
    template: dict[str, Any] = json.loads(out_path.read_text())
    out_path.unlink()
    return template


def _raise_for_api_error(response: requests.Response) -> None:
    if response.ok:
        return
    try:
        detail = response.json().get("error", {}).get("message", response.text)
    except ValueError:
        detail = response.text
    raise RuntimeError(
        f"{response.request.method} {response.url} -> {response.status_code}: {detail}"
    )


def get_credential() -> DeviceCodeCredential:
    def prompt(verification_uri: str, user_code: str, _expires_on: datetime) -> None:
        print(f"\nSign in: open {verification_uri} and enter code {user_code}\n")

    return DeviceCodeCredential(
        client_id=AZURE_CLI_CLIENT_ID,
        tenant_id="organizations",
        prompt_callback=prompt,
        cache_persistence_options=TokenCachePersistenceOptions(
            name=CACHE_NAME, allow_unencrypted_storage=True
        ),
    )


def pick_subscription(arm_token: str) -> tuple[str, str]:
    """Returns (subscription_id, tenant_id)."""
    resp = requests.get(
        f"{ARM_BASE}/subscriptions?api-version=2022-12-01",
        headers={"Authorization": f"Bearer {arm_token}"},
    )
    _raise_for_api_error(resp)
    subs = resp.json()["value"]
    if not subs:
        raise SystemExit("No subscriptions visible to this account.")
    if len(subs) == 1:
        chosen = subs[0]
    else:
        print("Multiple subscriptions found:")
        for i, sub in enumerate(subs):
            print(f"  [{i}] {sub['displayName']} ({sub['subscriptionId']})")
        idx = int(input("Pick one by number: "))
        chosen = subs[idx]
    return chosen["subscriptionId"], chosen["tenantId"]


def create_service_principal(graph_token: str) -> tuple[str, str, str]:
    """Returns (app_id, sp_object_id, client_secret)."""
    headers = {"Authorization": f"Bearer {graph_token}", "Content-Type": "application/json"}
    app_resp = requests.post(
        f"{GRAPH_BASE}/applications",
        headers=headers,
        json={"displayName": f"invoice-iq-{ENV_NAME}-sp"},
    )
    _raise_for_api_error(app_resp)
    app = app_resp.json()
    app_id, app_object_id = app["appId"], app["id"]

    sp_resp = requests.post(
        f"{GRAPH_BASE}/servicePrincipals", headers=headers, json={"appId": app_id}
    )
    _raise_for_api_error(sp_resp)
    sp_object_id = sp_resp.json()["id"]

    # No --skip-assignment equivalent needed: we never call the ARM role-assignment API
    # for this principal at all. Its only access comes from the Cosmos-specific
    # sqlRoleAssignments the cosmos.bicep deployment creates below (Article III).
    secret_resp = requests.post(
        f"{GRAPH_BASE}/applications/{app_object_id}/addPassword",
        headers=headers,
        json={"passwordCredential": {"displayName": "local-dev"}},
    )
    _raise_for_api_error(secret_resp)
    client_secret = secret_resp.json()["secretText"]

    return app_id, sp_object_id, client_secret


def ensure_resource_group(arm_token: str, subscription_id: str) -> None:
    resp = requests.put(
        f"{ARM_BASE}/subscriptions/{subscription_id}/resourcegroups/{RESOURCE_GROUP}"
        f"?api-version={ARM_API_VERSION}",
        headers={"Authorization": f"Bearer {arm_token}", "Content-Type": "application/json"},
        json={"location": LOCATION, "tags": TAGS},
    )
    _raise_for_api_error(resp)


def deploy_template(
    arm_token: str,
    subscription_id: str,
    deployment_name: str,
    template: dict[str, Any],
    parameters: dict[str, Any],
) -> dict[str, Any]:
    """Submits an ARM deployment and polls until it finishes. Returns the outputs dict."""
    url = (
        f"{ARM_BASE}/subscriptions/{subscription_id}/resourcegroups/{RESOURCE_GROUP}"
        f"/providers/Microsoft.Resources/deployments/{deployment_name}"
        f"?api-version={ARM_API_VERSION}"
    )
    body: dict[str, Any] = {
        "properties": {
            "mode": "Incremental",
            "template": template,
            "parameters": {k: {"value": v} for k, v in parameters.items()},
        }
    }
    headers = {"Authorization": f"Bearer {arm_token}", "Content-Type": "application/json"}
    resp = requests.put(url, headers=headers, json=body)
    _raise_for_api_error(resp)

    print(f"Deploying {deployment_name}...", end="", flush=True)
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        status_resp = requests.get(url, headers=headers)
        _raise_for_api_error(status_resp)
        state = status_resp.json()["properties"]["provisioningState"]
        if state == "Succeeded":
            print(" done.")
            outputs = status_resp.json()["properties"].get("outputs", {})
            return {k: v["value"] for k, v in outputs.items()}
        if state in ("Failed", "Canceled"):
            raise RuntimeError(f"{deployment_name} {state}: {status_resp.json()}")
        print(".", end="", flush=True)
        time.sleep(5)
    raise RuntimeError(f"{deployment_name} timed out after 10 minutes")


def update_env_file(values: dict[str, str]) -> None:
    content = ENV_PATH.read_text()
    for key, value in values.items():
        content = re.sub(rf"(?m)^{key}=.*$", f"{key}={value}", content)
    content = re.sub(
        r"(?m)^# --- Azure SQL \(Phase 2\) ---\nSQL_SERVER=.*\nSQL_DATABASE=.*\n", "", content
    )
    ENV_PATH.write_text(content)


def update_bicepparam(sp_object_id: str) -> None:
    content = BICEPPARAM_PATH.read_text()
    content = re.sub(
        r"cosmosDataAccessPrincipalId = '[0-9a-f-]+'",
        f"cosmosDataAccessPrincipalId = '{sp_object_id}'",
        content,
    )
    BICEPPARAM_PATH.write_text(content)


def main() -> int:
    credential = get_credential()
    arm_token = credential.get_token(f"{ARM_BASE}/.default").token
    subscription_id, tenant_id = pick_subscription(arm_token)

    graph_token = credential.get_token("https://graph.microsoft.com/.default").token
    print("Creating app registration + service principal...")
    client_id, sp_object_id, client_secret = create_service_principal(graph_token)

    print(f"Creating resource group {RESOURCE_GROUP}...")
    ensure_resource_group(arm_token, subscription_id)

    identity_template = compile_bicep(REPO_ROOT / "infra" / "modules" / "identity.bicep")
    identity_outputs = deploy_template(
        arm_token,
        subscription_id,
        "identity-deploy",
        identity_template,
        {"baseName": BASE_NAME, "environmentName": ENV_NAME, "location": LOCATION, "tags": TAGS},
    )

    cosmos_template = compile_bicep(REPO_ROOT / "infra" / "modules" / "cosmos.bicep")
    cosmos_outputs = deploy_template(
        arm_token,
        subscription_id,
        "cosmos-deploy",
        cosmos_template,
        {
            "baseName": BASE_NAME,
            "environmentName": ENV_NAME,
            "location": LOCATION,
            "tags": TAGS,
            "identityId": identity_outputs["id"],
            "principalId": identity_outputs["principalId"],
            "cosmosDataAccessPrincipalId": sp_object_id,
        },
    )

    update_env_file(
        {
            "AZURE_TENANT_ID": tenant_id,
            "AZURE_SUBSCRIPTION_ID": subscription_id,
            "AZURE_CLIENT_ID": client_id,
            "AZURE_CLIENT_SECRET": client_secret,
            "COSMOS_ENDPOINT": cosmos_outputs["accountEndpoint"],
            "COSMOS_DATABASE": cosmos_outputs["databaseName"],
        }
    )
    update_bicepparam(sp_object_id)

    print(f"\nDone. Cosmos endpoint: {cosmos_outputs['accountEndpoint']}")
    print(".env updated (client secret written there, not printed here).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"\nFailed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
