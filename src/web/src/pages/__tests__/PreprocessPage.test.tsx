import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PreprocessPage } from "../PreprocessPage";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

// The real send endpoint streams newline-delimited JSON, one file at a time, because
// each line corresponds to a real Document Intelligence round-trip finishing — this
// builds the same shape a real response would have.
function ndjsonResponse(lines: unknown[]): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const line of lines) {
        controller.enqueue(encoder.encode(`${JSON.stringify(line)}\n`));
      }
      controller.close();
    },
  });
  return new Response(body, {
    status: 200,
    headers: { "Content-Type": "application/x-ndjson" },
  });
}

// Same as ndjsonResponse but with a real gap between lines, so a test can observe an
// in-flight "N of M done" state instead of everything resolving in one microtask tick.
function delayedNdjsonResponse(lines: unknown[], delayMs = 15): Response {
  const encoder = new TextEncoder();
  let index = 0;
  const body = new ReadableStream<Uint8Array>({
    async pull(controller) {
      if (index >= lines.length) {
        controller.close();
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, delayMs));
      controller.enqueue(encoder.encode(`${JSON.stringify(lines[index])}\n`));
      index += 1;
    },
  });
  return new Response(body, { status: 200 });
}

function report(overrides: Record<string, unknown> = {}) {
  return {
    filename: "invoice.pdf",
    page_count: 1,
    content_hash: "abc",
    blank_page_numbers: [],
    exceeds_page_limit: false,
    would_reuse_cache: false,
    suggested_splits: [[1, 1]],
    original_size_bytes: 1000,
    optimized_size_bytes: 1000,
    error: null,
    ...overrides,
  };
}

function processingResult(overrides: Record<string, unknown> = {}) {
  return {
    invoice_id: "inv-1",
    invoice_number: "INV-1001",
    vendor_name: "Acme Supplies",
    decision: "straight_through",
    reason: "confidence:auto",
    overall_confidence: 0.97,
    match_status: "not_applicable",
    issues: [],
    payment_instruction: null,
    source_filename: "invoice.pdf",
    extraction_method: "azure_document_intelligence",
    review_outcome: null,
    ...overrides,
  };
}

function batchFileResult(filename: string, overrides: Record<string, unknown> = {}) {
  return {
    filename,
    status: "success",
    result: processingResult({ source_filename: filename }),
    error: null,
    ...overrides,
  };
}

describe("PreprocessPage", () => {
  let routes: Record<string, () => Response | Promise<Response>>;

  beforeEach(() => {
    routes = {};
    const fetchMock = vi.fn((url: string) => {
      const handler = routes[url];
      if (!handler) throw new Error(`unmocked fetch: ${url}`);
      return Promise.resolve(handler());
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("checks a single file, shows a completion status, and a batch ready to send", async () => {
    routes["/api/v1/preprocessing/check"] = () =>
      jsonResponse({ report: report({ blank_page_numbers: [2] }), batch_id: "batch-1" });

    render(<PreprocessPage />);
    const file = new File([new Uint8Array([1])], "invoice.pdf", { type: "application/pdf" });
    await userEvent.upload(screen.getByLabelText("Choose a PDF"), file);

    await waitFor(() => {
      expect(screen.getByTestId("single-check-complete")).toBeInTheDocument();
    });
    expect(screen.getByTestId("report-invoice.pdf")).toBeInTheDocument();
    expect(screen.getByText(/Blank page\(s\), dropped before sending: 2/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send for extraction" })).toBeInTheDocument();
  });

  it("shows the cache-hit badge and size reduction when present", async () => {
    routes["/api/v1/preprocessing/check"] = () =>
      jsonResponse({
        report: report({
          would_reuse_cache: true,
          original_size_bytes: 200000,
          optimized_size_bytes: 50000,
        }),
        batch_id: "batch-1",
      });

    render(<PreprocessPage />);
    const file = new File([new Uint8Array([1])], "invoice.pdf", { type: "application/pdf" });
    await userEvent.upload(screen.getByLabelText("Choose a PDF"), file);

    await waitFor(() => {
      expect(screen.getByText("cache hit")).toBeInTheDocument();
    });
    expect(screen.getByText(/75% smaller/)).toBeInTheDocument();
  });

  it("shows suggested splits when a file looks like more than one invoice", async () => {
    routes["/api/v1/preprocessing/check"] = () =>
      jsonResponse({
        report: report({
          page_count: 3,
          suggested_splits: [
            [1, 2],
            [3, 3],
          ],
        }),
        batch_id: "batch-1",
      });

    render(<PreprocessPage />);
    const file = new File([new Uint8Array([1])], "invoice.pdf", { type: "application/pdf" });
    await userEvent.upload(screen.getByLabelText("Choose a PDF"), file);

    await waitFor(() => {
      expect(screen.getByTestId("splits-invoice.pdf")).toHaveTextContent(
        "Looks like 2 separate invoices",
      );
    });
  });

  it("sends a checked file for extraction and shows the real result", async () => {
    routes["/api/v1/preprocessing/check"] = () =>
      jsonResponse({ report: report(), batch_id: "batch-1" });
    routes["/api/v1/preprocessing/batches/batch-1/send"] = () =>
      ndjsonResponse([
        { type: "progress", completed: 1, total: 1, filename: "invoice.pdf", status: "success" },
        { type: "done", results: [batchFileResult("invoice.pdf")] },
      ]);

    render(<PreprocessPage />);
    const file = new File([new Uint8Array([1])], "invoice.pdf", { type: "application/pdf" });
    await userEvent.upload(screen.getByLabelText("Choose a PDF"), file);
    const sendButton = await screen.findByRole("button", { name: "Send for extraction" });
    await userEvent.click(sendButton);

    await waitFor(() => {
      expect(screen.getByTestId("send-results")).toBeInTheDocument();
    });
    expect(screen.getByText(/Acme Supplies — INV-1001/)).toBeInTheDocument();
    expect(screen.getByText("straight through")).toBeInTheDocument();
  });

  it("shows a live N of M progress while a multi-file batch is being sent", async () => {
    routes["/api/v1/preprocessing/check/zip"] = () =>
      jsonResponse({
        summary: {
          total_files: 2,
          unreadable: 0,
          total_pages: 2,
          total_blank_pages: 0,
          exceeds_page_limit_count: 0,
          cache_hit_count: 0,
          total_original_size_bytes: 2000,
          total_optimized_size_bytes: 2000,
        },
        reports: [report({ filename: "a.pdf" }), report({ filename: "b.pdf" })],
        batch_id: "batch-2",
      });
    routes["/api/v1/preprocessing/batches/batch-2/send"] = () =>
      delayedNdjsonResponse([
        { type: "progress", completed: 1, total: 2, filename: "a.pdf", status: "success" },
        { type: "progress", completed: 2, total: 2, filename: "b.pdf", status: "success" },
        {
          type: "done",
          results: [batchFileResult("a.pdf"), batchFileResult("b.pdf")],
        },
      ]);

    render(<PreprocessPage />);
    const zip = new File([new Uint8Array([1])], "batch.zip", { type: "application/zip" });
    await userEvent.upload(screen.getByLabelText("Choose a .zip"), zip);
    const sendButton = await screen.findByRole("button", { name: "Send for extraction" });
    await userEvent.click(sendButton);

    // A real, in-flight count — not a simulated bar — observed before the batch finishes.
    await waitFor(() => {
      expect(screen.getByTestId("send-progress")).toHaveTextContent("1 of 2 done");
    });
    await waitFor(() => {
      expect(screen.getByTestId("send-results")).toBeInTheDocument();
    });
    expect(screen.getByText(/Sent — 2 invoice\(s\)/)).toBeInTheDocument();
  });

  it("shows an error message when a single file can't be read", async () => {
    routes["/api/v1/preprocessing/check"] = () =>
      jsonResponse({ detail: "could not read 'bad.pdf' as a PDF: garbage" }, 422);

    render(<PreprocessPage />);
    const file = new File([new Uint8Array([1])], "bad.pdf", { type: "application/pdf" });
    await userEvent.upload(screen.getByLabelText("Choose a PDF"), file);

    await waitFor(() => {
      expect(screen.getByTestId("single-check-error")).toHaveTextContent("could not read");
    });
  });

  it("shows an error if sending the batch fails", async () => {
    routes["/api/v1/preprocessing/check"] = () =>
      jsonResponse({ report: report(), batch_id: "batch-1" });
    routes["/api/v1/preprocessing/batches/batch-1/send"] = () =>
      jsonResponse({ detail: "batch already sent" }, 404);

    render(<PreprocessPage />);
    const file = new File([new Uint8Array([1])], "invoice.pdf", { type: "application/pdf" });
    await userEvent.upload(screen.getByLabelText("Choose a PDF"), file);
    const sendButton = await screen.findByRole("button", { name: "Send for extraction" });
    await userEvent.click(sendButton);

    await waitFor(() => {
      expect(screen.getByTestId("send-error")).toHaveTextContent("batch already sent");
    });
  });

  it("renders the batch tracker with a completion status and cache/size stats", async () => {
    routes["/api/v1/preprocessing/check/zip"] = () =>
      jsonResponse({
        summary: {
          total_files: 2,
          unreadable: 0,
          total_pages: 3,
          total_blank_pages: 1,
          exceeds_page_limit_count: 0,
          cache_hit_count: 1,
          total_original_size_bytes: 100000,
          total_optimized_size_bytes: 40000,
        },
        reports: [
          report({ filename: "a.pdf" }),
          report({ filename: "b.pdf", would_reuse_cache: true }),
        ],
        batch_id: "batch-2",
      });

    render(<PreprocessPage />);
    const zip = new File([new Uint8Array([1])], "batch.zip", { type: "application/zip" });
    await userEvent.upload(screen.getByLabelText("Choose a .zip"), zip);

    await waitFor(() => {
      expect(screen.getByTestId("batch-check-complete")).toHaveTextContent(
        "checked 2 file(s)",
      );
    });
    expect(screen.getByTestId("report-a.pdf")).toBeInTheDocument();
    expect(screen.getByTestId("report-b.pdf")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send for extraction" })).toBeInTheDocument();
  });

  it("shows the unreadable-file error inline within the batch instead of failing it", async () => {
    routes["/api/v1/preprocessing/check/zip"] = () =>
      jsonResponse({
        summary: {
          total_files: 2,
          unreadable: 1,
          total_pages: 1,
          total_blank_pages: 0,
          exceeds_page_limit_count: 0,
          cache_hit_count: 0,
          total_original_size_bytes: 1000,
          total_optimized_size_bytes: 1000,
        },
        reports: [
          report({ filename: "good.pdf" }),
          report({
            filename: "corrupt.pdf",
            page_count: 0,
            error: "could not read 'corrupt.pdf' as a PDF: garbage",
          }),
        ],
        batch_id: "batch-3",
      });

    render(<PreprocessPage />);
    const zip = new File([new Uint8Array([1])], "batch.zip", { type: "application/zip" });
    await userEvent.upload(screen.getByLabelText("Choose a .zip"), zip);

    await waitFor(() => {
      expect(screen.getByTestId("report-corrupt.pdf")).toHaveTextContent("could not read");
    });
    expect(screen.getByTestId("report-good.pdf")).toBeInTheDocument();
  });

  it("shows an empty-batch message and no send button when the zip has no PDFs", async () => {
    routes["/api/v1/preprocessing/check/zip"] = () =>
      jsonResponse({
        summary: {
          total_files: 0,
          unreadable: 0,
          total_pages: 0,
          total_blank_pages: 0,
          exceeds_page_limit_count: 0,
          cache_hit_count: 0,
          total_original_size_bytes: 0,
          total_optimized_size_bytes: 0,
        },
        reports: [],
        batch_id: null,
      });

    render(<PreprocessPage />);
    const zip = new File([new Uint8Array([1])], "batch.zip", { type: "application/zip" });
    await userEvent.upload(screen.getByLabelText("Choose a .zip"), zip);

    await waitFor(() => {
      expect(screen.getByTestId("batch-tracker-empty")).toBeInTheDocument();
    });
    expect(screen.queryByRole("button", { name: "Send for extraction" })).not.toBeInTheDocument();
  });

});
