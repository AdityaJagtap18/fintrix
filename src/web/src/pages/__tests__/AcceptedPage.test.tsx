import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AcceptedPage } from "../AcceptedPage";

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
  review_outcome: null,
  due_date: "2026-08-31",
};

const APPROVED_RESULT = {
  invoice_id: "inv-2",
  invoice_number: "INV-1042",
  vendor_name: "Beta Traders",
  decision: "exception",
  reason: "validation:BR-03",
  overall_confidence: 0.6,
  match_status: "not_applicable",
  issues: [],
  payment_instruction: null,
  extraction_method: "manual_entry",
  review_outcome: "approved",
  due_date: null,
};

const PENDING_RESULT = {
  invoice_id: "inv-3",
  invoice_number: "INV-2001",
  vendor_name: "Gamma Logistics",
  decision: "review",
  reason: "confidence:field_review",
  overall_confidence: 0.75,
  match_status: "not_applicable",
  issues: [],
  payment_instruction: null,
  extraction_method: "manual_entry",
  review_outcome: null,
  due_date: null,
};

const REJECTED_RESULT = {
  invoice_id: "inv-rejected-1",
  invoice_number: "INV-9999",
  vendor_name: "Delta Chemicals",
  decision: "exception",
  reason: "validation:BR-04",
  overall_confidence: 0.5,
  match_status: "not_applicable",
  issues: [{ rule_id: "BR-04", severity: "hard", message: "duplicate" }],
  payment_instruction: null,
  extraction_method: "manual_entry",
  review_outcome: "rejected",
  due_date: null,
};

describe("AcceptedPage", () => {
  let routes: Record<string, (init?: RequestInit) => Response | Promise<Response>>;

  beforeEach(() => {
    routes = {
      "/api/v1/processing/results": () => jsonResponse([]),
      "/api/v1/processing/results/rejected": () => jsonResponse([]),
    };
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      const handler = routes[url];
      if (!handler) throw new Error(`unmocked fetch: ${url}`);
      return Promise.resolve(handler(init));
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("shows a straight-through invoice with its payment instruction", async () => {
    routes["/api/v1/processing/results"] = () => jsonResponse([STRAIGHT_THROUGH_RESULT]);

    render(<AcceptedPage />);

    await waitFor(() => {
      expect(screen.getByText("Acme Supplies Pvt Ltd — INV-1001")).toBeInTheDocument();
    });
    expect(screen.getByText("straight through")).toBeInTheDocument();
    expect(screen.getByText(/1180.00 INR/)).toBeInTheDocument();
    expect(screen.getByText(/HDFC0001234-000111222333/)).toBeInTheDocument();
  });

  it("shows a human-approved invoice", async () => {
    routes["/api/v1/processing/results"] = () => jsonResponse([APPROVED_RESULT]);

    render(<AcceptedPage />);

    await waitFor(() => {
      expect(screen.getByText("Beta Traders — INV-1042")).toBeInTheDocument();
    });
    expect(screen.getByText("approved")).toBeInTheDocument();
  });

  it("excludes a pending invoice", async () => {
    routes["/api/v1/processing/results"] = () =>
      jsonResponse([STRAIGHT_THROUGH_RESULT, PENDING_RESULT]);

    render(<AcceptedPage />);

    await waitFor(() => {
      expect(screen.getByText("Acme Supplies Pvt Ltd — INV-1001")).toBeInTheDocument();
    });
    expect(screen.queryByText("Gamma Logistics — INV-2001")).not.toBeInTheDocument();
  });

  it("shows an empty state when nothing is accepted yet", async () => {
    routes["/api/v1/processing/results"] = () => jsonResponse([PENDING_RESULT]);

    render(<AcceptedPage />);

    await waitFor(() => {
      expect(screen.getByTestId("accepted-empty")).toBeInTheDocument();
    });
  });

  it("shows a rejected invoice in its own section", async () => {
    routes["/api/v1/processing/results/rejected"] = () => jsonResponse([REJECTED_RESULT]);

    render(<AcceptedPage />);

    await waitFor(() => {
      expect(screen.getByText("Delta Chemicals — INV-9999")).toBeInTheDocument();
    });
    expect(screen.getByText("rejected")).toBeInTheDocument();
    expect(screen.getByText("BR-04")).toBeInTheDocument();
  });

  it("shows an empty state when nothing is rejected", async () => {
    render(<AcceptedPage />);

    await waitFor(() => {
      expect(screen.getByTestId("rejected-empty")).toBeInTheDocument();
    });
  });

  it("shows an error message when the request fails", async () => {
    routes["/api/v1/processing/results"] = () => jsonResponse({ detail: "boom" }, 500);

    render(<AcceptedPage />);

    await waitFor(() => {
      expect(screen.getByTestId("accepted-error")).toBeInTheDocument();
    });
  });
});
