import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
  review_outcome: null,
  due_date: "2026-08-31",
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
  review_outcome: null,
  due_date: null,
};

function defaultSummary(overrides: Record<string, unknown> = {}) {
  return {
    total: 0,
    accepted_count: 0,
    rejected_count: 0,
    pending_count: 0,
    urgent_count: 0,
    due_soon_count: 0,
    ...overrides,
  };
}

describe("ProcessingResultsPage", () => {
  let routes: Record<string, (init?: RequestInit) => Response | Promise<Response>>;

  beforeEach(() => {
    routes = {
      "/api/v1/processing/summary": () => jsonResponse(defaultSummary()),
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

  it("shows an exception invoice with no payment instruction and its issue", async () => {
    routes["/api/v1/processing/results"] = () => jsonResponse([EXCEPTION_RESULT]);

    render(<ProcessingResultsPage onReview={vi.fn()} />);

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

  it("has no direct file-upload input, only a pointer to Preprocessing", async () => {
    routes["/api/v1/processing/results"] = () => jsonResponse([EXCEPTION_RESULT]);

    render(<ProcessingResultsPage onReview={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByTestId("upload-moved-notice")).toHaveTextContent("Preprocessing");
    });
    expect(screen.queryByLabelText("Choose a PDF")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Choose PDFs")).not.toBeInTheDocument();
  });

  it("excludes a straight-through invoice from the main list — it belongs on Accepted", async () => {
    routes["/api/v1/processing/results"] = () =>
      jsonResponse([STRAIGHT_THROUGH_RESULT, EXCEPTION_RESULT]);

    render(<ProcessingResultsPage onReview={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText("Acme Supplies Pvt Ltd — INV-1042")).toBeInTheDocument();
    });
    expect(screen.queryByText("Acme Supplies Pvt Ltd — INV-1001")).not.toBeInTheDocument();
  });

  it("excludes a human-approved invoice from the main list too", async () => {
    routes["/api/v1/processing/results"] = () =>
      jsonResponse([{ ...EXCEPTION_RESULT, review_outcome: "approved" }]);

    render(<ProcessingResultsPage onReview={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByTestId("tracker")).toBeInTheDocument();
    });
    expect(screen.queryByText("Acme Supplies Pvt Ltd — INV-1042")).not.toBeInTheDocument();
  });

  it("excludes a forwarded invoice from the main list too — it's done from AP's side", async () => {
    routes["/api/v1/processing/results"] = () =>
      jsonResponse([{ ...EXCEPTION_RESULT, review_outcome: "forwarded" }]);

    render(<ProcessingResultsPage onReview={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByTestId("tracker")).toBeInTheDocument();
    });
    expect(screen.queryByText("Acme Supplies Pvt Ltd — INV-1042")).not.toBeInTheDocument();
  });

  it("calls onReview with the invoice id when 'Review this invoice' is clicked", async () => {
    routes["/api/v1/processing/results"] = () => jsonResponse([EXCEPTION_RESULT]);
    const onReview = vi.fn();
    const user = userEvent.setup();

    render(<ProcessingResultsPage onReview={onReview} />);

    const reviewButton = await screen.findByRole("button", { name: "Review this invoice" });
    await user.click(reviewButton);

    expect(onReview).toHaveBeenCalledWith("inv-demo-flagged-1");
  });

  it("deletes an invoice when the confirmation is accepted", async () => {
    routes["/api/v1/processing/results"] = () => jsonResponse([EXCEPTION_RESULT]);
    let deleteCalled = false;
    routes["/api/v1/processing/results/inv-demo-flagged-1"] = (init) => {
      deleteCalled = init?.method === "DELETE";
      return new Response(null, { status: 204 });
    };
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();

    render(<ProcessingResultsPage onReview={vi.fn()} />);

    const deleteButton = await screen.findByRole("button", { name: "Delete" });
    await user.click(deleteButton);

    await waitFor(() => {
      expect(screen.queryByText("Acme Supplies Pvt Ltd — INV-1042")).not.toBeInTheDocument();
    });
    expect(deleteCalled).toBe(true);
  });

  it("does not delete when the confirmation is declined", async () => {
    routes["/api/v1/processing/results"] = () => jsonResponse([EXCEPTION_RESULT]);
    let deleteCalled = false;
    routes["/api/v1/processing/results/inv-demo-flagged-1"] = () => {
      deleteCalled = true;
      return new Response(null, { status: 204 });
    };
    vi.spyOn(window, "confirm").mockReturnValue(false);
    const user = userEvent.setup();

    render(<ProcessingResultsPage onReview={vi.fn()} />);

    const deleteButton = await screen.findByRole("button", { name: "Delete" });
    await user.click(deleteButton);

    expect(deleteCalled).toBe(false);
    expect(screen.getByText("Acme Supplies Pvt Ltd — INV-1042")).toBeInTheDocument();
  });

  it("shows real tracker numbers from GET /summary", async () => {
    routes["/api/v1/processing/results"] = () => jsonResponse([EXCEPTION_RESULT]);
    routes["/api/v1/processing/summary"] = () =>
      jsonResponse(
        defaultSummary({
          total: 5,
          accepted_count: 2,
          rejected_count: 1,
          pending_count: 2,
          urgent_count: 3,
          due_soon_count: 1,
        }),
      );

    render(<ProcessingResultsPage onReview={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByTestId("tracker-total")).toHaveTextContent("5");
    });
    expect(screen.getByTestId("tracker-urgent")).toHaveTextContent("3");
    expect(screen.getByTestId("tracker-due-soon")).toHaveTextContent("1");
    expect(screen.getByTestId("tracker-accepted")).toHaveTextContent("2");
    expect(screen.getByTestId("tracker-rejected")).toHaveTextContent("1");
    expect(screen.getByTestId("tracker-pending")).toHaveTextContent("2");
  });

  it("shows an error message when the request fails", async () => {
    routes["/api/v1/processing/results"] = () => jsonResponse({ detail: "boom" }, 500);

    render(<ProcessingResultsPage onReview={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByTestId("processing-error")).toBeInTheDocument();
    });
  });
});
