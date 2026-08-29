import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ExtractInvoiceForm } from "../ExtractInvoiceForm";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("ExtractInvoiceForm", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("URL", { ...URL, createObjectURL: vi.fn(() => "blob:fake-url") });
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("uploads the real file bytes and reports the result with its object URL", async () => {
    const onExtracted = vi.fn();
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        invoice_id: "inv-upload-abc",
        invoice_number: "INV-9999",
        vendor_name: "Real Vendor Ltd",
        decision: "review",
        reason: "policy:new_vendor",
        overall_confidence: 0.95,
        match_status: "not_applicable",
        issues: [],
        payment_instruction: null,
        source_filename: "real-invoice.pdf",
        extraction_method: "azure_document_intelligence",
      }),
    );

    render(<ExtractInvoiceForm onExtracted={onExtracted} />);

    const file = new File([new Uint8Array([1, 2, 3])], "real-invoice.pdf", {
      type: "application/pdf",
    });
    const input = screen.getByLabelText("Choose a PDF");
    await userEvent.upload(input, file);

    await waitFor(() => expect(onExtracted).toHaveBeenCalledTimes(1));

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/processing/extract");
    const formData = init.body as FormData;
    expect(formData.get("file")).toBeInstanceOf(File);

    const [result, fileUrl] = onExtracted.mock.calls[0];
    expect(result.invoice_number).toBe("INV-9999");
    expect(fileUrl).toBe("blob:fake-url");
  });

  it("shows extracting status while the request is in flight", async () => {
    let resolveFetch: (value: Response) => void = () => {};
    fetchMock.mockReturnValueOnce(
      new Promise<Response>((resolve) => {
        resolveFetch = resolve;
      }),
    );

    render(<ExtractInvoiceForm onExtracted={vi.fn()} />);
    const file = new File([new Uint8Array([1])], "a.pdf", { type: "application/pdf" });
    const input = screen.getByLabelText("Choose a PDF");
    await userEvent.upload(input, file);

    expect(screen.getByTestId("extracting-status")).toBeInTheDocument();

    resolveFetch(
      jsonResponse({
        invoice_id: "x",
        invoice_number: null,
        vendor_name: null,
        decision: "exception",
        reason: "validation:BR-01",
        overall_confidence: 0.0,
        match_status: "not_applicable",
        issues: [],
        payment_instruction: null,
        source_filename: "a.pdf",
        extraction_method: "azure_document_intelligence",
      }),
    );
    await waitFor(() => expect(screen.queryByTestId("extracting-status")).not.toBeInTheDocument());
  });

  it("shows an error message when extraction fails", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ detail: "Document Intelligence could not read this file: bad PDF" }, 422),
    );

    render(<ExtractInvoiceForm onExtracted={vi.fn()} />);
    const file = new File([new Uint8Array([1])], "bad.pdf", { type: "application/pdf" });
    const input = screen.getByLabelText("Choose a PDF");
    await userEvent.upload(input, file);

    await waitFor(() => {
      expect(screen.getByTestId("extract-error")).toHaveTextContent("bad PDF");
    });
  });
});
