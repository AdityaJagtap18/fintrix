import type { ReviewInvoice } from "../types";

/**
 * Placeholder invoice data. There is no `GET /api/v1/invoices/{id}` endpoint yet (the
 * plan.md §6 API surface lists one; it isn't built — see T-047's SQL repositories, which
 * this genuinely needs to be worth building for real rather than faking against nothing).
 * `claim`/`release`/`submit` in ReviewPage call the *real* backend; only the invoice
 * content itself — fields, lines, issues — is mocked here.
 *
 * The bounding regions below are placed to land within the 612x792pt (8.5x11in) blank
 * page in public/sample-invoice.pdf, in the DI-native unit of inches.
 */
export const mockInvoice: ReviewInvoice = {
  invoiceId: "inv-demo-1",
  documentId: "doc-demo-1",
  pdfUrl: "/sample-invoice.pdf",
  vendorName: "Acme Supplies Pvt Ltd",
  invoiceNumber: "INV-1042",
  fields: [
    {
      name: "vendor_name",
      value: "Acme Supplies Pvt Ltd",
      confidence: 0.98,
      page: 1,
      region: { page: 1, polygon: [0.75, 0.6, 3.5, 0.6, 3.5, 0.85, 0.75, 0.85] },
      source: "model",
      needsReview: false,
      suggestedValue: null,
    },
    {
      name: "invoice_number",
      value: "INV-1042",
      confidence: 0.99,
      page: 1,
      region: { page: 1, polygon: [6.0, 0.6, 7.5, 0.6, 7.5, 0.8, 6.0, 0.8] },
      source: "model",
      needsReview: false,
      suggestedValue: null,
    },
    {
      name: "invoice_date",
      value: "2026-08-15",
      confidence: 0.94,
      page: 1,
      region: { page: 1, polygon: [6.0, 0.9, 7.5, 0.9, 7.5, 1.1, 6.0, 1.1] },
      source: "model",
      needsReview: false,
      suggestedValue: null,
    },
    {
      name: "subtotal",
      value: "48250.00",
      confidence: 0.61,
      page: 1,
      region: { page: 1, polygon: [6.0, 6.5, 7.5, 6.5, 7.5, 6.75, 6.0, 6.75] },
      source: "model",
      needsReview: true,
      suggestedValue: null,
    },
    {
      name: "total",
      value: "51000.00",
      confidence: 0.58,
      page: 1,
      region: { page: 1, polygon: [6.0, 7.0, 7.5, 7.0, 7.5, 7.25, 6.0, 7.25] },
      source: "model",
      needsReview: true,
      // BR-03 recomputed this from subtotal + tax — a one-click accept (AC-02.3).
      suggestedValue: "50662.50",
    },
  ],
  lines: [
    { lineNumber: 1, description: "Office chairs (ergonomic)", quantity: 25, unitPrice: 1850.0, amount: 46250.0, productCode: "FUR-201", rowConfidence: 0.93 },
    { lineNumber: 2, description: "Desk lamps", quantity: 20, unitPrice: 100.0, amount: 2000.0, productCode: "FUR-330", rowConfidence: 0.9 },
  ],
  issues: [
    {
      ruleId: "BR-03",
      severity: "hard",
      passed: false,
      message: "subtotal + tax = 50662.50, total states 51000.00 (diff 337.50)",
      fields: ["total"],
      explanation:
        "The vendor's stated total (₹51,000.00) doesn't match subtotal plus tax (₹50,662.50). The difference is ₹337.50.",
    },
  ],
};
