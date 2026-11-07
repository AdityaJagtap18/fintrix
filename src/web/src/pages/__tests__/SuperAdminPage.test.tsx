import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SuperAdminPage } from "../SuperAdminPage";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const USERS = [
  {
    username: "ap",
    role: "ap",
    doc_quota: null,
    docs_used: 3,
    valid_until: null,
    created_at: "2026-01-01",
    status: "active",
  },
  {
    username: "demo1",
    role: "ap",
    doc_quota: 10,
    docs_used: 4,
    valid_until: "2099-01-01",
    created_at: "2026-01-01",
    status: "active",
  },
  {
    username: "demo2",
    role: "admin",
    doc_quota: 5,
    docs_used: 5,
    valid_until: "2020-01-01",
    created_at: "2026-01-01",
    status: "expired",
  },
];

describe("SuperAdminPage", () => {
  let routes: Record<string, (init?: RequestInit) => Response | Promise<Response>>;

  beforeEach(() => {
    sessionStorage.setItem("invoice-iq-token", "superadmin-token");
    sessionStorage.setItem("invoice-iq-role", "superadmin");
    routes = {
      "/api/v1/superadmin/users": () => jsonResponse(USERS),
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

  it("shows aggregate stats computed from the user list", async () => {
    render(<SuperAdminPage />);

    await waitFor(() => {
      expect(screen.getByTestId("superadmin-stat-users")).toHaveTextContent("3");
    });
    expect(screen.getByTestId("superadmin-stat-docs")).toHaveTextContent("12"); // 3+4+5
    expect(screen.getByTestId("superadmin-stat-active")).toHaveTextContent("2");
    expect(screen.getByTestId("superadmin-stat-expired")).toHaveTextContent("1");
  });

  it("lists every user with their usage and status", async () => {
    render(<SuperAdminPage />);

    await screen.findByTestId("user-table");
    expect(screen.getByText("3 (unlimited)")).toBeInTheDocument();
    expect(screen.getByText("4 / 10")).toBeInTheDocument();
    expect(screen.getByText("expired")).toBeInTheDocument();
    expect(screen.getAllByText("active")).toHaveLength(2);
  });

  it("shows an empty state when there are no users", async () => {
    routes["/api/v1/superadmin/users"] = () => jsonResponse([]);

    render(<SuperAdminPage />);

    await waitFor(() => {
      expect(screen.getByTestId("superadmin-empty")).toBeInTheDocument();
    });
  });

  it("shows a load error when the request fails", async () => {
    routes["/api/v1/superadmin/users"] = () => jsonResponse({ detail: "boom" }, 500);

    render(<SuperAdminPage />);

    await waitFor(() => {
      expect(screen.getByTestId("superadmin-load-error")).toHaveTextContent("boom");
    });
  });

  it("creates a user with a typed password and reveals it once, then refreshes the list", async () => {
    routes["/api/v1/superadmin/users"] = () => jsonResponse(USERS);
    const user = userEvent.setup();

    render(<SuperAdminPage />);
    await screen.findByTestId("user-table");

    routes["/api/v1/superadmin/users"] = (init?: RequestInit) => {
      if (init?.method === "POST") {
        const body = JSON.parse(init.body as string);
        return jsonResponse(
          {
            username: body.username,
            password: body.password,
            role: body.role,
            doc_quota: body.doc_quota ?? null,
            valid_until: null,
          },
          201,
        );
      }
      const newUser = {
        username: "newuser",
        role: "ap",
        doc_quota: null,
        docs_used: 0,
        valid_until: null,
        created_at: "2026-09-02",
        status: "active",
      };
      return jsonResponse([...USERS, newUser]);
    };

    await user.type(screen.getByLabelText(/Username/), "newuser");
    await user.type(screen.getByLabelText(/Password/), "hand-picked");
    await user.click(screen.getByRole("button", { name: "Create user" }));

    await waitFor(() => {
      expect(screen.getByTestId("created-user-password")).toHaveTextContent("hand-picked");
    });

    await waitFor(() => {
      expect(within(screen.getByTestId("user-table")).getByText("newuser")).toBeInTheDocument();
    });
  });

  it("shows an error when user creation fails", async () => {
    const user = userEvent.setup();
    render(<SuperAdminPage />);
    await screen.findByTestId("user-table");

    routes["/api/v1/superadmin/users"] = (init?: RequestInit) => {
      if (init?.method === "POST") return jsonResponse({ detail: "already exists" }, 409);
      return jsonResponse(USERS);
    };

    await user.type(screen.getByLabelText(/Username/), "ap");
    await user.click(screen.getByRole("button", { name: "Create user" }));

    await waitFor(() => {
      expect(screen.getByTestId("create-user-error")).toHaveTextContent("already exists");
    });
  });

  it("changes a user's password and reveals the new one once", async () => {
    const user = userEvent.setup();
    render(<SuperAdminPage />);
    await screen.findByTestId("user-table");

    routes["/api/v1/superadmin/users/demo1/password"] = (init?: RequestInit) => {
      const body = JSON.parse(init?.body as string);
      return jsonResponse({ username: "demo1", password: body.password ?? "generated-pw" });
    };

    await user.selectOptions(screen.getByLabelText("User"), "demo1");
    await user.type(screen.getByLabelText(/New password/), "a-new-password");
    await user.click(screen.getByRole("button", { name: "Change password" }));

    await waitFor(() => {
      expect(screen.getByTestId("changed-password-value")).toHaveTextContent("a-new-password");
    });
    expect(screen.getByTestId("password-changed-reveal")).toHaveTextContent("demo1");
  });

  it("generates a password when none is typed for the change-password form", async () => {
    const user = userEvent.setup();
    render(<SuperAdminPage />);
    await screen.findByTestId("user-table");

    routes["/api/v1/superadmin/users/demo1/password"] = () =>
      jsonResponse({ username: "demo1", password: "server-generated-pw" });

    await user.selectOptions(screen.getByLabelText("User"), "demo1");
    await user.click(screen.getByRole("button", { name: "Change password" }));

    await waitFor(() => {
      expect(screen.getByTestId("changed-password-value")).toHaveTextContent(
        "server-generated-pw",
      );
    });
  });

  it("shows an error when changing a password fails", async () => {
    const user = userEvent.setup();
    render(<SuperAdminPage />);
    await screen.findByTestId("user-table");

    routes["/api/v1/superadmin/users/demo1/password"] = () =>
      jsonResponse({ detail: "no user named 'demo1'" }, 404);

    await user.selectOptions(screen.getByLabelText("User"), "demo1");
    await user.click(screen.getByRole("button", { name: "Change password" }));

    await waitFor(() => {
      expect(screen.getByTestId("change-password-error")).toHaveTextContent("no user named");
    });
  });

  it("deletes a user after confirming, and refreshes the list", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    render(<SuperAdminPage />);
    await screen.findByTestId("user-table");

    routes["/api/v1/superadmin/users/demo1"] = () => new Response(null, { status: 204 });
    routes["/api/v1/superadmin/users"] = () => jsonResponse(USERS.filter((u) => u.username !== "demo1"));

    const row = within(screen.getByTestId("user-table")).getByText("demo1").closest("tr");
    if (!row) throw new Error("expected a table row for demo1");
    await user.click(within(row).getByRole("button", { name: "Delete" }));

    expect(confirmSpy).toHaveBeenCalled();
    await waitFor(() => {
      expect(within(screen.getByTestId("user-table")).queryByText("demo1")).not.toBeInTheDocument();
    });
    confirmSpy.mockRestore();
  });

  it("does nothing when the delete confirmation is declined", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    const user = userEvent.setup();
    render(<SuperAdminPage />);
    await screen.findByTestId("user-table");

    const row = within(screen.getByTestId("user-table")).getByText("demo1").closest("tr");
    if (!row) throw new Error("expected a table row for demo1");
    await user.click(within(row).getByRole("button", { name: "Delete" }));

    expect(confirmSpy).toHaveBeenCalled();
    expect(within(screen.getByTestId("user-table")).getByText("demo1")).toBeInTheDocument();
    confirmSpy.mockRestore();
  });

  it("shows an error when deleting a user fails, e.g. deleting yourself", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    render(<SuperAdminPage />);
    await screen.findByTestId("user-table");

    routes["/api/v1/superadmin/users/demo1"] = () =>
      jsonResponse({ detail: "you can't delete the account you're currently signed in as" }, 400);

    const row = within(screen.getByTestId("user-table")).getByText("demo1").closest("tr");
    if (!row) throw new Error("expected a table row for demo1");
    await user.click(within(row).getByRole("button", { name: "Delete" }));

    await waitFor(() => {
      expect(screen.getByTestId("delete-user-error")).toHaveTextContent("currently signed in");
    });
    vi.restoreAllMocks();
  });
});
