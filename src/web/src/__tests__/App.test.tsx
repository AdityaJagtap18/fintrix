import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";

// pdf.js can't render a real PDF under jsdom (no real 2D canvas context) — irrelevant
// here, since these tests never navigate into a Review workspace with a document.

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("App", () => {
  let routes: Record<string, (init?: RequestInit) => Response | Promise<Response>>;

  beforeEach(() => {
    sessionStorage.clear();
    routes = {
      "/api/v1/processing/results": () => jsonResponse([]),
      "/api/v1/processing/summary": () =>
        jsonResponse({
          total: 0,
          accepted_count: 0,
          rejected_count: 0,
          pending_count: 0,
          urgent_count: 0,
          due_soon_count: 0,
        }),
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
    sessionStorage.clear();
  });

  it("shows the login gate before anything else, with no tabs and no API calls", () => {
    render(<App />);

    expect(screen.getByRole("heading", { name: "Welcome back" })).toBeInTheDocument();
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Processed Invoices" })).not.toBeInTheDocument();
  });

  it("signs in as AP and shows every tab except Admin Panel", async () => {
    routes["/api/v1/auth/login"] = () => jsonResponse({ token: "ap-token", role: "ap" });
    const user = userEvent.setup();

    render(<App />);
    await user.type(screen.getByLabelText("Username"), "ap");
    await user.type(screen.getByLabelText("Password"), "correct-password");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: "Processed Invoices" })).toBeInTheDocument();
    });
    expect(screen.getByRole("tab", { name: "Review" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Preprocessing" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Accepted" })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Admin Panel" })).not.toBeInTheDocument();
  });

  it("signs in as admin and shows the Admin Panel tab too", async () => {
    routes["/api/v1/auth/login"] = () => jsonResponse({ token: "admin-token", role: "admin" });
    const user = userEvent.setup();

    render(<App />);
    await user.type(screen.getByLabelText("Username"), "admin");
    await user.type(screen.getByLabelText("Password"), "correct-password");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: "Admin Panel" })).toBeInTheDocument();
    });
  });

  it("shows an error on a failed login and stays on the gate", async () => {
    routes["/api/v1/auth/login"] = () =>
      jsonResponse({ detail: "incorrect username or password" }, 401);
    const user = userEvent.setup();

    render(<App />);
    await user.type(screen.getByLabelText("Username"), "ap");
    await user.type(screen.getByLabelText("Password"), "wrong");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => {
      expect(screen.getByTestId("app-login-error")).toHaveTextContent(
        "incorrect username or password",
      );
    });
    expect(screen.getByRole("heading", { name: "Welcome back" })).toBeInTheDocument();
  });

  it("uses a stored session without showing the login gate", async () => {
    sessionStorage.setItem("invoice-iq-token", "stored-token");
    sessionStorage.setItem("invoice-iq-role", "ap");

    render(<App />);

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: "Processed Invoices" })).toBeInTheDocument();
    });
    expect(screen.queryByRole("heading", { name: "Welcome back" })).not.toBeInTheDocument();
  });

  it("signs out and returns to the login gate", async () => {
    sessionStorage.setItem("invoice-iq-token", "stored-token");
    sessionStorage.setItem("invoice-iq-role", "admin");
    routes["/api/v1/auth/logout"] = () => new Response(null, { status: 204 });
    const user = userEvent.setup();

    render(<App />);
    await screen.findByRole("tab", { name: "Admin Panel" });

    await user.click(screen.getByRole("button", { name: "Sign out" }));

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Welcome back" })).toBeInTheDocument();
    });
    expect(sessionStorage.getItem("invoice-iq-token")).toBeNull();
    expect(sessionStorage.getItem("invoice-iq-role")).toBeNull();
  });

  it("signs in as superadmin and shows only the Super Admin dashboard, no tabs", async () => {
    routes["/api/v1/auth/login"] = () =>
      jsonResponse({ token: "superadmin-token", role: "superadmin" });
    routes["/api/v1/superadmin/users"] = () => jsonResponse([]);
    const user = userEvent.setup();

    render(<App />);
    await user.type(screen.getByLabelText("Username"), "root");
    await user.type(screen.getByLabelText("Password"), "correct-password");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Super Admin" })).toBeInTheDocument();
    });
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Processed Invoices" })).not.toBeInTheDocument();
    // Sign out stays available even without a tab bar.
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });

  it("uses a stored superadmin session without showing the login gate", async () => {
    sessionStorage.setItem("invoice-iq-token", "stored-token");
    sessionStorage.setItem("invoice-iq-role", "superadmin");
    routes["/api/v1/superadmin/users"] = () => jsonResponse([]);

    render(<App />);

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Super Admin" })).toBeInTheDocument();
    });
  });
});
