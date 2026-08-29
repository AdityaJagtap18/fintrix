import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ProcessingResultsPage } from "../ProcessingResultsPage";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const STRAIGHT_THROUGH_RESULT = {
  invoice_id: "inv-demo-clean-1",
  invoice_number: "INV-1001",
  vendor_name: "Acme Supplies Pvt Ltd",
  decision: "straight_through",
  reason: "confidence:auto",
  overall_confidence: 0.97,
  match_status: "not_applicable",
  issues: [],
  payment_instruction: {
    invoice_reference: "INV-1001",
    vendor_reference: "v-acme-001",
    vendor_bank_account: "HDFC0001234-000111222333",
    amount: "1180.00",
    currency: "INR",
    due_date: "2026-08-31",
    approval_evidence: "system:straight_through:confidence:auto",
  },
  extraction_method: "azure_document_intelligence",
};

const EXCEPTION_RESULT = {
  invoice_id: "inv-demo-flagged-1",
  invoice_number: "INV-1042",
  vendor_name: "Acme Supplies Pvt Ltd",
  decision: "exception",
  reason: "validation:BR-03",
  overall_confidence: 0.97,
  match_status: "not_applicable",
  issues: [{ rule_id: "BR-03", severity: "hard", message: "total mismatch" }],
  payment_instruction: null,
  extraction_method: "manual_entry",
};

describe("ProcessingResultsPage", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("renders a payment instruction for a straight-through invoice", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse([STRAIGHT_THROUGH_RESULT]));

    render(<ProcessingResultsPage />);

    await waitFor(() => {
      expect(screen.getByTestId("payment-instruction-inv-demo-clean-1")).toBeInTheDocument();
    });
    expect(screen.getByText("HDFC0001234-000111222333")).toBeInTheDocument();
    expect(screen.getByText("Real OCR (Azure Document Intelligence)")).toBeInTheDocument();
  });

  it("renders no payment instruction and shows the issue for an exception", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse([EXCEPTION_RESULT]));

    render(<ProcessingResultsPage />);

    await waitFor(() => {
      expect(screen.getByText("BR-03")).toBeInTheDocument();
    });
    expect(
      screen.queryByTestId("payment-instruction-inv-demo-flagged-1"),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText("No payment instruction — this invoice needs a human in the Review queue."),
    ).toBeInTheDocument();
    expect(screen.getByText("Manual test data")).toBeInTheDocument();
  });

  it("shows an error message when the request fails", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: "boom" }, 500));

    render(<ProcessingResultsPage />);

    await waitFor(() => {
      expect(screen.getByTestId("processing-error")).toBeInTheDocument();
    });
  });
});
