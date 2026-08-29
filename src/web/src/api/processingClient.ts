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
}

export async function fetchProcessingResults(): Promise<ProcessingResultDto[]> {
  const response = await fetch("/api/v1/processing/results");
  if (!response.ok) {
    throw new Error(`unexpected response from processing API: ${response.status}`);
  }
  return response.json();
}
