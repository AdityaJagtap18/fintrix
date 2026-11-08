"""FastAPI application entry point (plan.md §2, §6). `make run-api` serves this on :8000."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import auth, dashboard, invoices, review, vendors

app = FastAPI(title="FINTRIX Review API", version="0.1.0")

# Dev-only: the Vite dev server (:5173) calls this API (:8000) cross-origin. Tighten this
# to the real deployed frontend origin before this ever leaves a laptop.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
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
