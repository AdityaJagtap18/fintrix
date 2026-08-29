import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { UploadInvoiceForm } from "../UploadInvoiceForm";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("UploadInvoiceForm", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("loading a preset fills the form, and submitting posts it with nulls for blanks", async () => {
    const onSubmitted = vi.fn();
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        invoice_id: "inv-upload-abc",
        invoice_number: "INV-9001",
        vendor_name: "Beta Traders",
        decision: "straight_through",
        reason: "confidence:auto",
        overall_confidence: 0.97,
        match_status: "not_applicable",
        issues: [],
        payment_instruction: null,
        source_filename: null,
      }),
    );

    render(<UploadInvoiceForm onSubmitted={onSubmitted} />);

    fireEvent.change(screen.getByLabelText("Load a fake invoice"), {
      target: { value: "clean" },
    });
    expect(screen.getByLabelText("Vendor name")).toHaveValue("Beta Traders");

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Process invoice" }));

    await waitFor(() => expect(onSubmitted).toHaveBeenCalledTimes(1));

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/processing/process");
    const body = JSON.parse(init.body);
    expect(body.vendor_name).toBe("Beta Traders");
    expect(body.po_number).toBeNull(); // blank optional field becomes null, not ""
  });

  it("shows an error message if the submission fails", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: "total is required" }, 422));

    render(<UploadInvoiceForm onSubmitted={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("Load a fake invoice"), {
      target: { value: "clean" },
    });

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Process invoice" }));

    await waitFor(() => {
      expect(screen.getByTestId("upload-error")).toHaveTextContent("total is required");
    });
  });

  it("captures the picked file's name without sending its contents", async () => {
    render(<UploadInvoiceForm onSubmitted={vi.fn()} />);
    const file = new File(["not-a-real-pdf"], "beta-traders-inv-9001.pdf", {
      type: "application/pdf",
    });
    const input = screen.getByLabelText("Invoice file (PDF)") as HTMLInputElement;

    await userEvent.upload(input, file);

    fireEvent.change(screen.getByLabelText("Load a fake invoice"), {
      target: { value: "clean" },
    });
    // Loading a preset after picking a file should keep the file name attached.
    expect(input.files?.[0].name).toBe("beta-traders-inv-9001.pdf");
  });
});
