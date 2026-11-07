import { authHeader } from "./authClient";
import type { ReviewInvoice } from "../types";

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
  // "azure_document_intelligence" for a real POST /extract call, "manual_entry" for
  // POST /process (including the two seeded demo invoices).
  extraction_method: string;
  // Set once a reviewer has actually submitted a decision — looked up from the review
  // API's own store, not something processing.py tracks on its own. "forwarded" sits in
  // the admin queue until accepted (-> "approved"); AP can never submit "approved" itself.
  review_outcome: "approved" | "rejected" | "forwarded" | null;
  // The invoice's own extracted due_date, set regardless of decision — unlike
  // payment_instruction.due_date, which only exists once a payment instruction does.
  due_date: string | null;
}

export interface ProcessingSummaryDto {
  total: number;
  accepted_count: number;
  rejected_count: number;
  pending_count: number;
  urgent_count: number;
  due_soon_count: number;
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
  const response = await fetch("/api/v1/processing/results", { headers: authHeader() });
  if (!response.ok) {
    throw new Error(`unexpected response from processing API: ${response.status}`);
  }
  return response.json();
}

// Aggregate counts only, computed server-side across every processed invoice — rejected
// ones included, unlike GET /results.
export async function fetchProcessingSummary(): Promise<ProcessingSummaryDto> {
  const response = await fetch("/api/v1/processing/summary", { headers: authHeader() });
  if (!response.ok) {
    throw new Error(`unexpected response from processing API: ${response.status}`);
  }
  return response.json();
}

export async function fetchRejectedResults(): Promise<ProcessingResultDto[]> {
  const response = await fetch("/api/v1/processing/results/rejected", {
    headers: authHeader(),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: "unknown error" }));
    throw new Error(
      typeof body.detail === "string" ? body.detail : `unexpected response: ${response.status}`,
    );
  }
  return response.json();
}

export async function deleteProcessingResult(invoiceId: string): Promise<void> {
  const response = await fetch(`/api/v1/processing/results/${invoiceId}`, {
    method: "DELETE",
    headers: authHeader(),
  });
  if (!response.ok) {
    throw new Error(`unexpected response from processing API: ${response.status}`);
  }
}

export async function submitInvoice(
  request: ProcessInvoiceRequest,
): Promise<ProcessingResultDto> {
  const response = await fetch("/api/v1/processing/process", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeader() },
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

// Wire shape of GET /results/{id} — snake_case, same convention as ProcessingResultDto
// above. Mapped to the camelCase ReviewInvoice the Review page actually consumes below.
interface FieldDetailDto {
  name: string;
  value: string | number | null;
  confidence: number;
  page: number | null;
  region: { page: number; polygon: number[] } | null;
  source: "model" | "human" | "rule" | "default";
  needs_review: boolean;
  suggested_value: string | number | null;
}

interface ValidationIssueDetailDto {
  rule_id: string;
  severity: "hard" | "soft";
  passed: boolean;
  message: string;
  fields: string[];
}

interface InvoiceDetailDto {
  invoice_id: string;
  document_id: string;
  vendor_name: string | null;
  invoice_number: string | null;
  pdf_url: string | null;
  fields: FieldDetailDto[];
  issues: ValidationIssueDetailDto[];
}

// Fetches whatever this backend process actually processed for `invoiceId` — seeded demo
// invoices included — replacing ReviewPage.tsx's previously-hardcoded mockInvoice.
export async function fetchInvoiceDetail(invoiceId: string): Promise<ReviewInvoice> {
  const response = await fetch(`/api/v1/processing/results/${invoiceId}`, {
    headers: authHeader(),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: "unknown error" }));
    throw new Error(
      typeof body.detail === "string" ? body.detail : `unexpected response: ${response.status}`,
    );
  }
  const dto: InvoiceDetailDto = await response.json();
  return {
    invoiceId: dto.invoice_id,
    documentId: dto.document_id,
    pdfUrl: dto.pdf_url,
    vendorName: dto.vendor_name,
    invoiceNumber: dto.invoice_number,
    lines: [], // not surfaced by the backend yet — nothing in the Review page reads it
    fields: dto.fields.map((f) => ({
      name: f.name,
      value: f.value,
      confidence: f.confidence,
      page: f.page,
      region: f.region,
      source: f.source,
      needsReview: f.needs_review,
      suggestedValue: f.suggested_value,
    })),
    issues: dto.issues.map((i) => ({
      ruleId: i.rule_id,
      severity: i.severity,
      passed: i.passed,
      message: i.message,
      fields: i.fields,
      explanation: null, // AC-14.3: no explainer wired in here yet
    })),
  };
}
