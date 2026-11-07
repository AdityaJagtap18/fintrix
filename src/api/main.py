"""FastAPI application entry point (plan.md §2, §6). `make run-api` serves this on :8000."""

from __future__ import annotations

import os

from fastapi import FastAPI

from api.routers import admin, auth, preprocessing, processing, review, superadmin

app = FastAPI(title="Invoice IQ Review API", version="0.1.0")
app.include_router(review.router)
app.include_router(processing.router)
app.include_router(preprocessing.router)
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(superadmin.router)


def _telemetry_enabled() -> bool:
    # PYTEST_VERSION is set by pytest itself for the whole session (pytest>=7.2) —
    # exactly so code like this can detect "running under pytest" without importing
    # pytest. Never send real telemetry from a test run: api.main is imported by every
    # test that builds a TestClient, and configure_azure_monitor's exporters are
    # fire-and-forget background senders — nothing would fail, but every `pytest`/
    # `make check` run would quietly dump fake request traffic into the real
    # Application Insights resource this connection string points at, drowning out the
    # genuine usage it's meant to show.
    if "PYTEST_VERSION" in os.environ:
        return False
    return bool(os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING"))


if _telemetry_enabled():
    from azure.monitor.opentelemetry import configure_azure_monitor

    from api.dependencies import get_azure_credential

    # Exports traces (every request, with latency) and the api.observability counters
    # (OpenTelemetry metrics API — see that module) to Azure Monitor. No connection
    # string, no import, no call: local dev without Application Insights configured
    # behaves exactly as it did before this was added.
    #
    # credential=... — same Article III reasoning as every other Azure client in this
    # codebase (Cosmos, Document Intelligence): ingestion authenticates via Entra ID,
    # not the connection string's InstrumentationKey alone. Without this, ingestion is
    # rejected outright on a resource with local/key-based auth disabled.
    configure_azure_monitor(
        connection_string=os.environ["APPLICATIONINSIGHTS_CONNECTION_STRING"],
        credential=get_azure_credential(),
        logger_name="invoice_iq",
    )
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app)


@app.get("/health/live")
def health_live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
def health_ready() -> dict[str, str]:
    # Once real repositories exist (T-047), this should verify DB connectivity too.
    return {"status": "ok"}
