"""Admin endpoints: the queue of invoices AP has forwarded, the accept action that
finalizes one, and the bank-payment Excel export of everything accepted. Every endpoint
here requires an admin session (this router's own dependencies=[Depends(
get_admin_session)], api/auth.py) — one sign-in gates the whole app, and the "admin" role
on top of that is what unlocks this router specifically. An AP session is real and valid
everywhere else but never satisfies this.

Column layout for the export is a generic NEFT/RTGS-style bulk-payment template
(vendor name, IFSC code, account number, amount, currency, invoice number, due date,
reference) — not validated against any specific bank's real upload spec. Adjust
_EXPORT_COLUMNS/_row_for below if a real target format differs.
"""

from __future__ import annotations

import io
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from openpyxl import Workbook
from pydantic import BaseModel

import api.routers.processing as processing_module
from api.auth import get_admin_session
from api.dependencies import now_utc
from api.routers.processing import (
    ProcessingResultDto,
    Repos,
    ReviewRepo,
    _all_results,
    _is_accepted,
)
from core.models import ReviewOutcome
from core.review.assignment import NotClaimedByReviewerError
from core.review.assignment import claim as claim_invoice
from core.review.assignment import submit as submit_invoice

router = APIRouter(
    prefix="/api/v1/admin", tags=["admin"], dependencies=[Depends(get_admin_session)]
)

# The reviewer identity recorded on the assignment for every admin accept — there's one
# shared admin login, not per-person accounts (see api/auth.py's docstring), so there's
# no individual name to attribute this to beyond "admin".
_ADMIN_REVIEWER = "admin"


def _is_forwarded(result: ProcessingResultDto) -> bool:
    return result.review_outcome == ReviewOutcome.FORWARDED.value


@router.get("/forwarded", response_model=list[ProcessingResultDto])
def get_forwarded_results(review_repo: ReviewRepo, repos: Repos) -> list[ProcessingResultDto]:
    return [r for r in _all_results(review_repo, repos) if _is_forwarded(r)]


class AcceptResponse(BaseModel):
    result: ProcessingResultDto


@router.post("/{invoice_id}/accept", response_model=AcceptResponse)
def accept_invoice(
    invoice_id: str,
    review_repo: ReviewRepo,
    repos: Repos,
) -> AcceptResponse:
    assignment = review_repo.get(invoice_id)
    if assignment is None or assignment.outcome != ReviewOutcome.FORWARDED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"invoice {invoice_id!r} is not currently forwarded for acceptance",
        )
    # AP's submit(FORWARDED) already set submitted_at, which clears is_claimed — this
    # claim is a fresh one, not a conflict with AP's now-closed claim. See
    # core.review.assignment.claim's docstring for why a fresh claim after submission is
    # always available.
    now = now_utc()
    claim_invoice(invoice_id, _ADMIN_REVIEWER, now, review_repo)
    try:
        submit_invoice(invoice_id, _ADMIN_REVIEWER, ReviewOutcome.APPROVED, now, review_repo)
    except NotClaimedByReviewerError as exc:  # pragma: no cover - claim_invoice just above
        # guarantees this holds; kept only so a future refactor can't silently swallow it
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    result = processing_module._store.accept_forwarded(invoice_id, repos)
    return AcceptResponse(result=result)


# --- Excel export ---

_EXPORT_COLUMNS = (
    "Vendor Name",
    "IFSC Code",
    "Account Number",
    "Amount",
    "Currency",
    "Invoice Number",
    "Due Date",
    "Reference",
)


def _split_bank_account(bank_account: str | None) -> tuple[str, str]:
    """This app's own vendor bank_account values are consistently formatted
    '<IFSC>-<account number>' (see the seeded demo vendor and every test fixture) — split
    on the first '-'. Falls back to putting the whole string in the account-number column
    if it isn't in that shape, rather than dropping data silently."""
    if not bank_account or "-" not in bank_account:
        return "", bank_account or ""
    ifsc, _, account_number = bank_account.partition("-")
    return ifsc, account_number


def _row_for(result: ProcessingResultDto) -> tuple[str, ...]:
    instruction = result.payment_instruction
    ifsc, account_number = _split_bank_account(
        instruction.vendor_bank_account if instruction else None
    )
    return (
        result.vendor_name or "",
        ifsc,
        account_number,
        str(instruction.amount) if instruction and instruction.amount is not None else "",
        (instruction.currency or "") if instruction else "",
        result.invoice_number or "",
        instruction.due_date.isoformat() if instruction and instruction.due_date else "",
        (instruction.invoice_reference or "") if instruction else "",
    )


def _build_export_workbook(results: list[ProcessingResultDto]) -> bytes:
    accepted = [r for r in results if _is_accepted(r)]
    workbook = Workbook()
    sheet = workbook.active
    if sheet is None:  # pragma: no cover - openpyxl always gives a fresh Workbook one
        raise RuntimeError("Workbook() has no active worksheet — should be unreachable")
    sheet.title = "Accepted Invoices"
    sheet.append(_EXPORT_COLUMNS)
    for result in accepted:
        sheet.append(_row_for(result))
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


@router.get("/export")
def export_accepted_invoices(review_repo: ReviewRepo, repos: Repos) -> Response:
    content = _build_export_workbook(_all_results(review_repo, repos))
    filename = f"accepted-invoices-{datetime.now().date().isoformat()}.xlsx"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
