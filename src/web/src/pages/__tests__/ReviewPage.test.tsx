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
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ detail: { message: "already claimed", held_by: "rajesh" } }, 409),
    );

    render(<ReviewPage />);

    await waitFor(() => {
      expect(screen.getByTestId("claim-status-message")).toHaveTextContent(
        "already claimed by rajesh",
      );
    });
  });

  it("renders the field editor and issue panel once the claim succeeds", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ invoice_id: "inv-demo-1", claimed_by: "priya", expires_at: "2026-01-01T00:00:00" }),
    );

    render(<ReviewPage />);

    await waitFor(() => {
      expect(screen.getByRole("list", { name: "Extracted fields" })).toBeInTheDocument();
    });
    expect(screen.getByRole("list", { name: "Validation issues" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
  });

  it("submits an approval and shows the confirmation message", async () => {
    fetchMock
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
    render(<ReviewPage />);

    const approveButton = await screen.findByRole("button", { name: "Approve" });
    await user.click(approveButton);

    await waitFor(() => {
      expect(screen.getByTestId("submitted-message")).toHaveTextContent("Review approved");
    });

    const submitCall = fetchMock.mock.calls[1];
    expect(submitCall[0]).toBe("/api/v1/review/inv-demo-1/submit");
    expect(JSON.parse(submitCall[1].body)).toEqual({ outcome: "approved" });
  });

  it("shows an error message if submission fails without losing the editor state", async () => {
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse({ invoice_id: "inv-demo-1", claimed_by: "priya", expires_at: "2026-01-01T00:00:00" }),
      )
      .mockResolvedValueOnce(jsonResponse({ detail: "not claimed" }, 403));

    const user = userEvent.setup();
    render(<ReviewPage />);

    const approveButton = await screen.findByRole("button", { name: "Approve" });
    await user.click(approveButton);

    await waitFor(() => {
      expect(screen.getByTestId("submit-error")).toBeInTheDocument();
    });
    // The editor is still on screen — a failed submit doesn't strand the reviewer.
    expect(screen.getByRole("list", { name: "Extracted fields" })).toBeInTheDocument();
  });

  it("releases the claim with keepalive on pagehide, not just on unmount", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ invoice_id: "inv-demo-1", claimed_by: "priya", expires_at: "2026-01-01T00:00:00" }),
    );

    render(<ReviewPage />);
    await screen.findByRole("button", { name: "Approve" });

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
