import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../../auth/AuthContext";
import { ReviewPage } from "../ReviewPage";

// DocumentViewer renders a real PDF via pdf.js canvas APIs that jsdom can't back
// meaningfully (no real 2D canvas context) — it degrades to its own error state, which is
// fine and doesn't block these tests; they cover the claim/submit flow around it, not PDF
// rendering itself.

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const DEMO_INVOICE = {
  invoiceId: "inv-demo-1",
  documentId: "doc-demo-1",
  pdfUrl: "/sample-invoice.pdf",
  vendorName: "Acme Supplies Pvt Ltd",
  invoiceNumber: "INV-1042",
  fields: [
    {
      name: "total",
      value: "51000.00",
      confidence: 0.58,
      page: null,
      region: null,
      source: "model",
      needsReview: true,
      suggestedValue: "50662.50",
    },
  ],
  lines: [],
  issues: [
    {
      ruleId: "BR-03",
      severity: "hard",
      passed: false,
      message: "subtotal + tax mismatch",
      fields: ["total"],
      explanation: "The total doesn't match subtotal plus tax.",
    },
  ],
};

function renderReviewPage(invoiceId = "inv-demo-1") {
  return render(
    <MemoryRouter initialEntries={[`/review/${invoiceId}`]}>
      <AuthProvider>
        <Routes>
          <Route path="/review/:invoiceId" element={<ReviewPage />} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe("ReviewPage", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    // Base fallback for calls beyond what a test explicitly queues — most notably the
    // fire-and-forget `release` call ReviewPage's unmount cleanup makes, which otherwise
    // races testing-library's auto-cleanup and can hit the *real* fetch with a relative
    // URL after this mock is unstubbed. mockResolvedValueOnce calls below still take
    // priority in order; this only backstops what's left over.
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    // Explicit, not relying on testing-library's auto-cleanup timing relative to this
    // hook: unmount (which fires ReviewPage's fire-and-forget `release` call against the
    // still-stubbed mock) must happen strictly before the stub is torn down, or that call
    // hits the real global fetch with a relative URL and blows up outside any test.
    cleanup();
    vi.unstubAllGlobals();
  });

  it("shows the claim conflict message when the invoice is already claimed", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(DEMO_INVOICE))
      .mockResolvedValueOnce(
        jsonResponse({ detail: { message: "already claimed", held_by: "rajesh" } }, 409),
      );

    renderReviewPage();

    await waitFor(() => {
      expect(screen.getByTestId("claim-status-message")).toHaveTextContent(
        "already claimed by rajesh",
      );
    });
  });

  it("renders the field editor and issue panel once the claim succeeds", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(DEMO_INVOICE))
      .mockResolvedValueOnce(
        jsonResponse({ invoice_id: "inv-demo-1", claimed_by: "priya", expires_at: "2026-01-01T00:00:00" }),
      );

    renderReviewPage();

    await waitFor(() => {
      expect(screen.getByRole("list", { name: "Extracted fields" })).toBeInTheDocument();
    });
    expect(screen.getByRole("list", { name: "Validation issues" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
  });

  it("submits an approval and shows the confirmation message", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(DEMO_INVOICE))
      .mockResolvedValueOnce(
        jsonResponse({ invoice_id: "inv-demo-1", claimed_by: "priya", expires_at: "2026-01-01T00:00:00" }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          invoice_id: "inv-demo-1",
          claimed_by: "priya",
          submitted_at: "2026-01-01T00:05:00",
          outcome: "approved",
        }),
      );

    const user = userEvent.setup();
    renderReviewPage();

    const approveButton = await screen.findByRole("button", { name: "Approve" });
    await user.click(approveButton);

    await waitFor(() => {
      expect(screen.getByTestId("submitted-message")).toHaveTextContent("Review approved");
    });

    const submitCall = fetchMock.mock.calls[2];
    expect(submitCall[0]).toBe("/api/v1/review/inv-demo-1/submit");
    expect(JSON.parse(submitCall[1].body)).toEqual({ outcome: "approved" });
  });

  it("shows an error message if submission fails without losing the editor state", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(DEMO_INVOICE))
      .mockResolvedValueOnce(
        jsonResponse({ invoice_id: "inv-demo-1", claimed_by: "priya", expires_at: "2026-01-01T00:00:00" }),
      )
      .mockResolvedValueOnce(jsonResponse({ detail: "not claimed" }, 403));

    const user = userEvent.setup();
    renderReviewPage();

    const approveButton = await screen.findByRole("button", { name: "Approve" });
    await user.click(approveButton);

    await waitFor(() => {
      expect(screen.getByTestId("submit-error")).toBeInTheDocument();
    });
    // The editor is still on screen — a failed submit doesn't strand the reviewer.
    expect(screen.getByRole("list", { name: "Extracted fields" })).toBeInTheDocument();
  });
});
