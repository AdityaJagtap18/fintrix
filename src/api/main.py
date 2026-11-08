"""FastAPI application entry point (plan.md §2, §6). `make run-api` serves this on :8000."""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import auth, dashboard, invoices, review, vendors

app = FastAPI(title="FINTRIX Review API", version="0.1.0")

# Defaults to the Vite dev server's origin (:5173 calling :8000 cross-origin). In
# production, nginx serves the frontend and proxies /api/* to this backend under the
# same domain, so the browser's calls are same-origin and CORS doesn't come into play at
# all — ALLOWED_ORIGINS exists for the rarer case something still calls this API directly
# from a different origin (e.g. a future staging frontend on its own subdomain).
_allowed_origins = os.environ.get("ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in _allowed_origins.split(",") if origin.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(review.router)
app.include_router(auth.router)
app.include_router(invoices.router)
app.include_router(vendors.router)
app.include_router(dashboard.router)


@app.get("/health/live")
def health_live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
def health_ready() -> dict[str, str]:
    # Once real repositories exist (T-047), this should verify DB connectivity too.
    return {"status": "ok"}
