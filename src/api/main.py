"""FastAPI application entry point (plan.md §2, §6). `make run-api` serves this on :8000."""

from __future__ import annotations

from fastapi import FastAPI

from api.routers import processing, review

app = FastAPI(title="Invoice IQ Review API", version="0.1.0")
app.include_router(review.router)
app.include_router(processing.router)


@app.get("/health/live")
def health_live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
def health_ready() -> dict[str, str]:
    # Once real repositories exist (T-047), this should verify DB connectivity too.
    return {"status": "ok"}
