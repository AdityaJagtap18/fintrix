// Mirrors src/api/routers/processing.py's Pydantic response models exactly, snake_case
// included — see types.ts's note on ClaimResponseDto/AssignmentResponseDto for why.

export interface IssueDto {
  rule_id: string;
  severity: "hard" | "soft";
  message: string;
}

export interface PaymentInstructionDto {
  invoice_reference: string;
  vendor_reference: string;
  vendor_bank_account: string | null;
  amount: string | null;
  currency: string | null;
  due_date: string | null;
  approval_evidence: string;
}

export interface ProcessingResultDto {
  invoice_id: string;
  invoice_number: string | null;
  vendor_name: string | null;
  decision: string;
  reason: string;
  overall_confidence: number;
  match_status: string;
  issues: IssueDto[];
  payment_instruction: PaymentInstructionDto | null;
  source_filename: string | null;
}

// What POST /process accepts — the fields a real OCR extraction would have produced.
// There is no live Document Intelligence resource wired in (Phase 2), so the uploaded
// file's bytes are never read; `source_filename` is carried through for display only.
export interface ProcessInvoiceRequest {
  vendor_name: string;
  vendor_tax_id: string | null;
  invoice_number: string;
  invoice_date: string; // YYYY-MM-DD
  due_date: string | null;
  po_number: string | null;
  currency: string;
  subtotal: string | null;
  tax_total: string | null;
  total: string;
  amount_due: string | null;
  confidence: number; // 0-1
  vendor_requires_po: boolean;
  vendor_invoices_processed: number;
  vendor_bank_account: string | null;
  source_filename: string | null;
}

export async function fetchProcessingResults(): Promise<ProcessingResultDto[]> {
  const response = await fetch("/api/v1/processing/results");
  if (!response.ok) {
    throw new Error(`unexpected response from processing API: ${response.status}`);
  }
  return response.json();
}

export async function submitInvoice(
  request: ProcessInvoiceRequest,
): Promise<ProcessingResultDto> {
  const response = await fetch("/api/v1/processing/process", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: "unknown error" }));
    throw new Error(
      typeof body.detail === "string" ? body.detail : `unexpected response: ${response.status}`,
    );
  }
  return response.json();
}
