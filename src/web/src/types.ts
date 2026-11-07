// Mirrors the shapes in src/core/models.py — kept as plain types (not classes) since
// everything here is just what the API sends over the wire.

export type FieldSource = "model" | "human" | "rule" | "default";

export interface BoundingRegion {
  page: number;
  /** Flat [x1, y1, x2, y2, ...] in inches — Document Intelligence's native unit. */
  polygon: number[];
}

export interface ExtractedField {
  name: string;
  value: string | number | null;
  confidence: number;
  page: number | null;
  region: BoundingRegion | null;
  source: FieldSource;
  needsReview: boolean;
  /** A rule-suggested correction (e.g. a recomputed total) — AC-02.3's one-click accept. */
  suggestedValue: string | number | null;
}

export interface LineItem {
  lineNumber: number;
  description: string;
  quantity: number;
  unitPrice: number;
  amount: number;
  productCode: string | null;
  rowConfidence: number;
}

export type Severity = "hard" | "soft";

export interface ValidationIssue {
  ruleId: string;
  severity: Severity;
  passed: boolean;
  message: string;
  fields: string[];
  /** FR-068/AC-14.1: plain-language explanation. Absent when the explainer is unavailable
   * (AC-14.3) — the UI must fall back to `message` in that case, never block on it. */
  explanation: string | null;
}

export interface ReviewInvoice {
  invoiceId: string;
  documentId: string;
  // null when this invoice has no real file behind it (manual entry, seeded demo data) —
  // only a real Document Intelligence upload has bytes to render.
  pdfUrl: string | null;
  vendorName: string | null;
  invoiceNumber: string | null;
  fields: ExtractedField[];
  lines: LineItem[];
  issues: ValidationIssue[];
}

// "approved" is set only by an admin accepting a forwarded invoice (api/routers/admin.py)
// — AP itself can only submit "rejected" or "forwarded" (api/routers/review.py rejects
// "approved" from AP with a 403).
export type ReviewOutcome = "approved" | "rejected" | "forwarded";

// These two mirror src/api/routers/review.py's Pydantic response models *exactly*,
// snake_case included — FastAPI serializes Pydantic field names as-is, and there is no
// camelCase alias configured on those models. Don't "fix" these to camelCase without
// adding a `populate_by_name` + `alias_generator` config on the Python side to match.
export interface ClaimResponseDto {
  invoice_id: string;
  claimed_by: string;
  expires_at: string;
}

export interface AssignmentResponseDto {
  invoice_id: string;
  claimed_by: string | null;
  submitted_at: string | null;
  outcome: ReviewOutcome | null;
}
