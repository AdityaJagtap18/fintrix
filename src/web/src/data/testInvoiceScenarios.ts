import type { ProcessInvoiceRequest } from "../api/processingClient";

// Five scenarios covering the routing decisions that matter, mirrored 1:1 against
// tests/unit/test_processing_api.py's upload tests so the UI and the test suite agree on
// what each one actually does. "27AAPFU0939F1ZV" is the same checksum-valid GSTIN used
// throughout the codebase (scripts/demo_process_invoice.py, api/routers/processing.py).
export interface TestInvoiceScenario {
  id: string;
  label: string;
  description: string;
  request: ProcessInvoiceRequest;
}

export const TEST_INVOICE_SCENARIOS: TestInvoiceScenario[] = [
  {
    id: "clean",
    label: "Clean invoice — straight through",
    description: "Established vendor, exact arithmetic, high confidence. Should auto-post.",
    request: {
      vendor_name: "Beta Traders",
      vendor_tax_id: "27AAPFU0939F1ZV",
      invoice_number: "INV-9001",
      invoice_date: "2026-08-10",
      due_date: "2026-09-09",
      po_number: null,
      currency: "INR",
      subtotal: "2000.00",
      tax_total: "360.00",
      total: "2360.00",
      amount_due: "2360.00",
      confidence: 0.97,
      vendor_requires_po: false,
      vendor_invoices_processed: 25,
      vendor_bank_account: "ICIC0009999-111222333",
      source_filename: null,
    },
  },
  {
    id: "mismatch",
    label: "Total doesn't reconcile (BR-03)",
    description: "Stated total is ₹140 off subtotal + tax. Should stop as an exception.",
    request: {
      vendor_name: "Beta Traders",
      vendor_tax_id: "27AAPFU0939F1ZV",
      invoice_number: "INV-9002",
      invoice_date: "2026-08-11",
      due_date: "2026-09-10",
      po_number: null,
      currency: "INR",
      subtotal: "2000.00",
      tax_total: "360.00",
      total: "2500.00", // should be 2360.00
      amount_due: "2500.00",
      confidence: 0.97,
      vendor_requires_po: false,
      vendor_invoices_processed: 25,
      vendor_bank_account: "ICIC0009999-111222333",
      source_filename: null,
    },
  },
  {
    id: "new-vendor",
    label: "First invoice from this vendor",
    description: "Zero processing history. Never eligible for straight-through (AC-01.3).",
    request: {
      vendor_name: "Gamma Logistics",
      vendor_tax_id: null,
      invoice_number: "INV-9003",
      invoice_date: "2026-08-12",
      due_date: "2026-09-11",
      po_number: null,
      currency: "INR",
      subtotal: "1300.00",
      tax_total: "234.00",
      total: "1534.00",
      amount_due: "1534.00",
      confidence: 0.97,
      vendor_requires_po: false,
      vendor_invoices_processed: 0,
      vendor_bank_account: "SBIN0007777-444555666",
      source_filename: null,
    },
  },
  {
    id: "low-confidence",
    label: "Low OCR confidence",
    description: "Below the auto threshold on every field. Routes to human review.",
    request: {
      vendor_name: "Delta Chemicals",
      vendor_tax_id: null,
      invoice_number: "INV-9004",
      invoice_date: "2026-08-13",
      due_date: "2026-09-12",
      po_number: null,
      currency: "INR",
      subtotal: "800.00",
      tax_total: "144.00",
      total: "944.00",
      amount_due: "944.00",
      confidence: 0.75,
      vendor_requires_po: false,
      vendor_invoices_processed: 30,
      vendor_bank_account: "AXIS0003333-222111000",
      source_filename: null,
    },
  },
  {
    id: "duplicate",
    label: "Duplicate of INV-1001 (Acme)",
    description: "Same vendor tax ID + invoice number as the already-processed demo invoice.",
    request: {
      vendor_name: "Acme Supplies Pvt Ltd",
      vendor_tax_id: "27AAPFU0939F1ZV",
      invoice_number: "INV-1001",
      invoice_date: "2026-08-01",
      due_date: "2026-08-31",
      po_number: null,
      currency: "INR",
      subtotal: "1000.00",
      tax_total: "180.00",
      total: "1180.00",
      amount_due: "1180.00",
      confidence: 0.97,
      vendor_requires_po: false,
      vendor_invoices_processed: 50,
      vendor_bank_account: "HDFC0001234-000111222333",
      source_filename: null,
    },
  },
];
