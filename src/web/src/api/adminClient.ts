// Mirrors src/api/routers/admin.py. Every function here requires an admin-role session
// (authClient.ts's login) — the backend enforces this independently (get_admin_session),
// these functions just supply the header.
import { authHeader } from "./authClient";
import type { ProcessingResultDto } from "./processingClient";

async function throwForErrorResponse(response: Response): Promise<never> {
  const body = await response.json().catch(() => ({ detail: "unknown error" }));
  throw new Error(
    typeof body.detail === "string" ? body.detail : `unexpected response: ${response.status}`,
  );
}

export async function fetchForwardedResults(): Promise<ProcessingResultDto[]> {
  const response = await fetch("/api/v1/admin/forwarded", { headers: authHeader() });
  if (!response.ok) return throwForErrorResponse(response);
  return response.json();
}

interface AcceptResponseDto {
  result: ProcessingResultDto;
}

export async function acceptInvoice(invoiceId: string): Promise<ProcessingResultDto> {
  const response = await fetch(`/api/v1/admin/${invoiceId}/accept`, {
    method: "POST",
    headers: authHeader(),
  });
  if (!response.ok) return throwForErrorResponse(response);
  const body: AcceptResponseDto = await response.json();
  return body.result;
}

// Triggers a real file download of the bank-payment .xlsx — the viewer's sandbox blocks
// script-driven saves (see the Artifact tool's own note on this same restriction), but
// this app isn't a sandboxed artifact: a plain object-URL anchor click works fine in the
// actual browser this is served to.
export async function downloadAcceptedInvoicesExport(): Promise<void> {
  const response = await fetch("/api/v1/admin/export", { headers: authHeader() });
  if (!response.ok) return throwForErrorResponse(response);
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const filenameMatch = /filename="([^"]+)"/.exec(disposition);
  const filename = filenameMatch?.[1] ?? "accepted-invoices.xlsx";
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
