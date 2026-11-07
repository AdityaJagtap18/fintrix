import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AdminPage } from "../AdminPage";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const FORWARDED_RESULT = {
  invoice_id: "inv-forwarded-1",
  invoice_number: "INV-3001",
  vendor_name: "Zeta Print Co",
  decision: "review",
  reason: "confidence:field_review",
  overall_confidence: 0.8,
  match_status: "not_applicable",
  issues: [{ rule_id: "BR-11", severity: "soft", message: "vendor name fuzzy match" }],
  payment_instruction: null,
  extraction_method: "manual_entry",
  review_outcome: "forwarded",
  due_date: null,
};

const ACCEPTED_RESULT = {
  invoice_id: "inv-clean-1",
  invoice_number: "INV-1001",
  vendor_name: "Acme Supplies Pvt Ltd",
  decision: "straight_through",
  reason: "auto:straight_through",
  overall_confidence: 0.98,
  match_status: "matched",
  issues: [],
  payment_instruction: null,
  extraction_method: "manual_entry",
  review_outcome: null,
  due_date: null,
};

const EMPTY_METRICS = {
  files_checked: 0,
  blank_pages_dropped: 0,
  oversized_rejected: 0,
  total_original_bytes: 0,
  total_optimized_bytes: 0,
  di_calls_made: 0,
  di_calls_avoided: 0,
  di_pages_billed: 0,
  di_pages_avoided: 0,
  cache_hit_rate: 0,
  cost_per_page: null,
  estimated_cost_billed: null,
  estimated_cost_avoided: null,
  pipeline_runs: 0,
  pipeline_success_count: 0,
  pipeline_error_count: 0,
  avg_pipeline_latency_seconds: 0,
  avg_field_completion_rate: 0,
  avg_confidence_score: 0,
  hitl_triggered_count: 0,
  hitl_trigger_rate: 0,
};

describe("AdminPage", () => {
  let routes: Record<string, (init?: RequestInit) => Response | Promise<Response>>;

  beforeEach(() => {
    sessionStorage.setItem("invoice-iq-token", "admin-token");
    sessionStorage.setItem("invoice-iq-role", "admin");
    routes = {
      "/api/v1/admin/forwarded": () => jsonResponse([]),
      "/api/v1/processing/results": () => jsonResponse([]),
      "/api/v1/processing/results/rejected": () => jsonResponse([]),
      "/api/v1/preprocessing/metrics": () => jsonResponse(EMPTY_METRICS),
    };
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      const handler = routes[url];
      if (!handler) throw new Error(`unmocked fetch: ${url}`);
      return Promise.resolve(handler(init));
    });
    vi.stubGlobal("fetch", fetchMock);
    // jsdom doesn't implement object URLs — the export download path needs both.
    vi.stubGlobal(
      "URL",
      Object.assign(URL, { createObjectURL: vi.fn(() => "blob:mock"), revokeObjectURL: vi.fn() }),
    );
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    sessionStorage.clear();
  });

  it("loads and shows the dashboard with real counts", async () => {
    routes["/api/v1/admin/forwarded"] = () => jsonResponse([FORWARDED_RESULT]);
    routes["/api/v1/processing/results"] = () => jsonResponse([ACCEPTED_RESULT]);

    render(<AdminPage />);

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Admin Dashboard" })).toBeInTheDocument();
    });
    expect(screen.getByTestId("admin-stat-forwarded")).toHaveTextContent("1");
    expect(screen.getByTestId("admin-stat-accepted")).toHaveTextContent("1");
    expect(screen.queryByText("Zeta Print Co — INV-3001")).not.toBeInTheDocument();
  });

  it("shows the observability panel with real preprocessing metrics", async () => {
    routes["/api/v1/preprocessing/metrics"] = () =>
      jsonResponse({
        ...EMPTY_METRICS,
        files_checked: 12,
        di_calls_made: 8,
        di_calls_avoided: 4,
        cache_hit_rate: 0.5,
        pipeline_success_count: 7,
        pipeline_error_count: 1,
        avg_confidence_score: 0.91,
        hitl_trigger_rate: 0.25,
      });

    render(<AdminPage />);

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Observability" })).toBeInTheDocument();
    });
    expect(screen.getByTestId("admin-metric-files-checked")).toHaveTextContent("12");
    expect(screen.getByTestId("admin-metric-cache-hit-rate")).toHaveTextContent("50%");
    expect(screen.getByTestId("admin-metric-avg-confidence")).toHaveTextContent("91%");
    expect(screen.getByTestId("admin-metric-hitl-rate")).toHaveTextContent("25%");
    expect(screen.getByRole("heading", { name: "Document Intelligence calls" })).toBeInTheDocument();
    expect(screen.getByText("Made (8)")).toBeInTheDocument();
    expect(screen.getByText("Avoided (cache) (4)")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Pipeline outcomes" })).toBeInTheDocument();
  });

  it("navigates from the dashboard into the forwarded queue and back", async () => {
    routes["/api/v1/admin/forwarded"] = () => jsonResponse([FORWARDED_RESULT]);
    const user = userEvent.setup();

    render(<AdminPage />);
    await screen.findByRole("heading", { name: "Admin Dashboard" });

    await user.click(screen.getByRole("button", { name: "View forwarded queue →" }));

    await waitFor(() => {
      expect(screen.getByText("Zeta Print Co — INV-3001")).toBeInTheDocument();
    });
    expect(screen.getByText("BR-11")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "← Back to dashboard" }));

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Admin Dashboard" })).toBeInTheDocument();
    });
  });

  it("accepts an invoice and removes it from the queue", async () => {
    routes["/api/v1/admin/forwarded"] = () => jsonResponse([FORWARDED_RESULT]);
    routes["/api/v1/admin/inv-forwarded-1/accept"] = () =>
      jsonResponse({
        result: { ...FORWARDED_RESULT, review_outcome: "approved", payment_instruction: null },
      });
    const user = userEvent.setup();

    render(<AdminPage />);
    await screen.findByRole("heading", { name: "Admin Dashboard" });
    await user.click(screen.getByRole("button", { name: "View forwarded queue →" }));
    await screen.findByText("Zeta Print Co — INV-3001");

    await user.click(screen.getByRole("button", { name: "Accept" }));

    await waitFor(() => {
      expect(screen.queryByText("Zeta Print Co — INV-3001")).not.toBeInTheDocument();
    });
  });

  it("shows an empty state when nothing is forwarded", async () => {
    const user = userEvent.setup();

    render(<AdminPage />);
    await screen.findByRole("heading", { name: "Admin Dashboard" });
    await user.click(screen.getByRole("button", { name: "View forwarded queue →" }));

    await waitFor(() => {
      expect(screen.getByTestId("admin-empty")).toBeInTheDocument();
    });
  });

  it("shows a load error when the initial fetch fails", async () => {
    routes["/api/v1/admin/forwarded"] = () => jsonResponse({ detail: "boom" }, 500);

    render(<AdminPage />);

    await waitFor(() => {
      expect(screen.getByTestId("admin-load-error")).toHaveTextContent("boom");
    });
  });

  it("downloads the export when the export button is clicked", async () => {
    routes["/api/v1/admin/export"] = () =>
      new Response(new Blob(["fake xlsx bytes"]), {
        status: 200,
        headers: {
          "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          "Content-Disposition": 'attachment; filename="accepted-invoices-2026-09-02.xlsx"',
        },
      });
    const user = userEvent.setup();

    render(<AdminPage />);
    await screen.findByRole("heading", { name: "Admin Dashboard" });

    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    await user.click(screen.getByRole("button", { name: /Export accepted invoices/ }));

    await waitFor(() => expect(clickSpy).toHaveBeenCalled());
    clickSpy.mockRestore();
  });

  it("shows an error if the export fails", async () => {
    routes["/api/v1/admin/export"] = () => jsonResponse({ detail: "boom" }, 500);
    const user = userEvent.setup();

    render(<AdminPage />);
    await screen.findByRole("heading", { name: "Admin Dashboard" });

    await user.click(screen.getByRole("button", { name: /Export accepted invoices/ }));

    await waitFor(() => {
      expect(screen.getByTestId("admin-export-error")).toHaveTextContent("boom");
    });
  });
});
