import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
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

// GET /api/v1/processing/results/{id} — the first call ReviewPage makes, before it ever
// claims anything. Fields are empty (FieldEditor's list wrapper renders regardless);
// issues needs at least one failed entry, or IssuePanel renders its "no failures" text
// instead of a role="list" — these tests assert on that list existing.
function detailResponse(): Response {
  return jsonResponse({
    invoice_id: "inv-demo-1",
    document_id: "doc-demo-1",
    vendor_name: "Acme Supplies Pvt Ltd",
    invoice_number: "INV-1042",
    pdf_url: null,
    fields: [],
    issues: [{ rule_id: "BR-03", severity: "hard", passed: false, message: "total mismatch", fields: [] }],
  });
}

describe("ReviewPage", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  let onSubmitted: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    onSubmitted = vi.fn();
    // App.tsx is what gates sign-in now — ReviewPage only ever renders once someone's
    // already signed in, so every test here starts pre-authenticated.
    sessionStorage.setItem("invoice-iq-token", "test-token");
    sessionStorage.setItem("invoice-iq-role", "ap");
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
    sessionStorage.clear();
  });

  it("prompts to pick an invoice when none is selected, without loading anything", () => {
    render(<ReviewPage invoiceId={null} onSubmitted={onSubmitted} />);

    expect(
      screen.getByText("Pick an invoice from Processing Results to review it."),
    ).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("shows the claim conflict message when the invoice is already claimed", async () => {
    fetchMock
      .mockResolvedValueOnce(detailResponse())
      .mockResolvedValueOnce(
        jsonResponse({ detail: { message: "already claimed", held_by: "rajesh" } }, 409),
      );

    render(<ReviewPage invoiceId="inv-demo-1" onSubmitted={onSubmitted} />);

    await waitFor(() => {
      expect(screen.getByTestId("claim-status-message")).toHaveTextContent(
        "already claimed by rajesh",
      );
    });
  });

  it("renders the field editor and issue panel once the claim succeeds", async () => {
    fetchMock
      .mockResolvedValueOnce(detailResponse())
      .mockResolvedValueOnce(
        jsonResponse({ invoice_id: "inv-demo-1", claimed_by: "priya", expires_at: "2026-01-01T00:00:00" }),
      );

    render(<ReviewPage invoiceId="inv-demo-1" onSubmitted={onSubmitted} />);

    await waitFor(() => {
      expect(screen.getByRole("list", { name: "Extracted fields" })).toBeInTheDocument();
    });
    expect(screen.getByRole("list", { name: "Validation issues" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Forward for acceptance" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reject" })).toBeInTheDocument();
  });

  it("submits a forward and sends the reviewer straight to Processed Invoices", async () => {
    fetchMock
      .mockResolvedValueOnce(detailResponse())
      .mockResolvedValueOnce(
        jsonResponse({ invoice_id: "inv-demo-1", claimed_by: "priya", expires_at: "2026-01-01T00:00:00" }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          invoice_id: "inv-demo-1",
          claimed_by: "priya",
          submitted_at: "2026-01-01T00:05:00",
          outcome: "forwarded",
        }),
      );

    const user = userEvent.setup();
    render(<ReviewPage invoiceId="inv-demo-1" onSubmitted={onSubmitted} />);

    const forwardButton = await screen.findByRole("button", { name: "Forward for acceptance" });
    await user.click(forwardButton);

    await waitFor(() => {
      expect(onSubmitted).toHaveBeenCalledTimes(1);
    });

    const submitCall = fetchMock.mock.calls[2];
    expect(submitCall[0]).toBe("/api/v1/review/inv-demo-1/submit");
    expect(JSON.parse(submitCall[1].body)).toEqual({ outcome: "forwarded" });
    expect(submitCall[1].headers.Authorization).toBe("Bearer test-token");
  });

  it("submits a rejection and sends the reviewer straight to Processed Invoices", async () => {
    fetchMock
      .mockResolvedValueOnce(detailResponse())
      .mockResolvedValueOnce(
        jsonResponse({ invoice_id: "inv-demo-1", claimed_by: "priya", expires_at: "2026-01-01T00:00:00" }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          invoice_id: "inv-demo-1",
          claimed_by: "priya",
          submitted_at: "2026-01-01T00:05:00",
          outcome: "rejected",
        }),
      );

    const user = userEvent.setup();
    render(<ReviewPage invoiceId="inv-demo-1" onSubmitted={onSubmitted} />);

    const rejectButton = await screen.findByRole("button", { name: "Reject" });
    await user.click(rejectButton);

    await waitFor(() => {
      expect(onSubmitted).toHaveBeenCalledTimes(1);
    });
  });

  it("shows an error message if submission fails without losing the editor state", async () => {
    fetchMock
      .mockResolvedValueOnce(detailResponse())
      .mockResolvedValueOnce(
        jsonResponse({ invoice_id: "inv-demo-1", claimed_by: "priya", expires_at: "2026-01-01T00:00:00" }),
      )
      .mockResolvedValueOnce(jsonResponse({ detail: "not claimed" }, 403));

    const user = userEvent.setup();
    render(<ReviewPage invoiceId="inv-demo-1" onSubmitted={onSubmitted} />);

    const forwardButton = await screen.findByRole("button", { name: "Forward for acceptance" });
    await user.click(forwardButton);

    await waitFor(() => {
      expect(screen.getByTestId("submit-error")).toBeInTheDocument();
    });
    // The editor is still on screen — a failed submit doesn't strand the reviewer.
    expect(screen.getByRole("list", { name: "Extracted fields" })).toBeInTheDocument();
  });

  it("shows an inline message when the session has expired, rather than redirecting", async () => {
    fetchMock
      .mockResolvedValueOnce(detailResponse())
      .mockResolvedValueOnce(jsonResponse({ detail: "sign-in required" }, 401));

    render(<ReviewPage invoiceId="inv-demo-1" onSubmitted={onSubmitted} />);

    await waitFor(() => {
      expect(screen.getByTestId("claim-status-message")).toHaveTextContent(
        "Your session has expired",
      );
    });
  });

  it("releases the claim with keepalive on pagehide, not just on unmount", async () => {
    fetchMock
      .mockResolvedValueOnce(detailResponse())
      .mockResolvedValueOnce(
        jsonResponse({ invoice_id: "inv-demo-1", claimed_by: "priya", expires_at: "2026-01-01T00:00:00" }),
      );

    render(<ReviewPage invoiceId="inv-demo-1" onSubmitted={onSubmitted} />);
    await screen.findByRole("button", { name: "Forward for acceptance" });

    fetchMock.mockClear();
    window.dispatchEvent(new Event("pagehide"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/review/inv-demo-1/release");
    expect(init.keepalive).toBe(true);

    // Unmounting afterwards must not release a second time — pagehide already did it.
    fetchMock.mockClear();
    cleanup();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
