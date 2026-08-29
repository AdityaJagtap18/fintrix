import type { AssignmentResponseDto, ClaimResponseDto, ReviewOutcome } from "../types";

const API_BASE = "/api/v1/review";

export class AlreadyClaimedError extends Error {
  constructor(
    public readonly invoiceId: string,
    public readonly heldBy: string,
  ) {
    super(`invoice ${invoiceId} is already claimed by ${heldBy}`);
    this.name = "AlreadyClaimedError";
  }
}

export class ForbiddenError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ForbiddenError";
  }
}

export class UnauthorizedError extends Error {
  constructor() {
    super("not authenticated");
    this.name = "UnauthorizedError";
  }
}

function authHeaders(reviewer: string): HeadersInit {
  // Dev-only placeholder for Entra ID bearer auth (T-062) — see src/api/dependencies.py.
  // Replacing this means replacing exactly this function, nowhere else in the client.
  return { "X-Reviewer": reviewer };
}

async function throwForErrorResponse(response: Response, invoiceId: string): Promise<never> {
  if (response.status === 401) throw new UnauthorizedError();
  if (response.status === 409) {
    const body = await response.json();
    throw new AlreadyClaimedError(invoiceId, body.detail?.held_by ?? "unknown");
  }
  if (response.status === 403) {
    const body = await response.json().catch(() => ({ detail: "forbidden" }));
    throw new ForbiddenError(body.detail ?? "forbidden");
  }
  throw new Error(`unexpected response from review API: ${response.status}`);
}

export interface ClaimResult {
  invoiceId: string;
  claimedBy: string;
  expiresAt: string;
}

export async function claimInvoice(invoiceId: string, reviewer: string): Promise<ClaimResult> {
  const response = await fetch(`${API_BASE}/${invoiceId}/claim`, {
    method: "POST",
    headers: authHeaders(reviewer),
  });
  if (!response.ok) return throwForErrorResponse(response, invoiceId);
  const dto: ClaimResponseDto = await response.json();
  return { invoiceId: dto.invoice_id, claimedBy: dto.claimed_by, expiresAt: dto.expires_at };
}

export async function releaseInvoice(invoiceId: string, reviewer: string): Promise<void> {
  const response = await fetch(`${API_BASE}/${invoiceId}/release`, {
    method: "POST",
    headers: authHeaders(reviewer),
  });
  if (!response.ok) await throwForErrorResponse(response, invoiceId);
}

export interface SubmitResult {
  invoiceId: string;
  claimedBy: string | null;
  submittedAt: string | null;
  outcome: ReviewOutcome | null;
}

export async function submitReview(
  invoiceId: string,
  reviewer: string,
  outcome: ReviewOutcome,
): Promise<SubmitResult> {
  const response = await fetch(`${API_BASE}/${invoiceId}/submit`, {
    method: "POST",
    headers: { ...authHeaders(reviewer), "Content-Type": "application/json" },
    body: JSON.stringify({ outcome }),
  });
  if (!response.ok) return throwForErrorResponse(response, invoiceId);
  const dto: AssignmentResponseDto = await response.json();
  return {
    invoiceId: dto.invoice_id,
    claimedBy: dto.claimed_by,
    submittedAt: dto.submitted_at,
    outcome: dto.outcome,
  };
}
