import { useEffect, useState } from "react";
import {
  changePassword,
  createUser,
  deleteUser,
  fetchUsers,
  type UserSummaryDto,
} from "../api/superadminClient";
import { BarChart } from "../components/BarChart";
import { DonutChart } from "../components/DonutChart";

type LoadState = "loading" | "loaded" | "error";

/**
 * The superadmin's one dashboard: every user's usage/expiry, and forms to create,
 * reset the password of, or remove AP/admin demo accounts. This is the whole thing —
 * superadmin doesn't process invoices, so there's nothing else here (see
 * api/routers/superadmin.py's docstring for why that's deliberate, not a placeholder
 * for more).
 */
export function SuperAdminPage() {
  const [users, setUsers] = useState<UserSummaryDto[]>([]);
  const [state, setState] = useState<LoadState>("loading");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [deletingUsername, setDeletingUsername] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setState("loading");
    fetchUsers()
      .then((data) => {
        if (cancelled) return;
        setUsers(data);
        setState("loaded");
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setErrorMessage(err instanceof Error ? err.message : "Could not load users.");
        setState("error");
      });
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  async function handleDeleteUser(username: string) {
    if (!window.confirm(`Delete user "${username}"? This can't be undone.`)) return;
    setDeletingUsername(username);
    setDeleteError(null);
    try {
      await deleteUser(username);
      setRefreshKey((k) => k + 1);
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : "Could not delete this user.");
    } finally {
      setDeletingUsername(null);
    }
  }

  const totalDocsUsed = users.reduce((sum, u) => sum + u.docs_used, 0);
  const activeCount = users.filter((u) => u.status === "active").length;
  const expiredCount = users.filter((u) => u.status === "expired").length;

  return (
    <div className="superadmin-page">
      <h1>Super Admin</h1>

      {state === "loaded" && (
        <div className="dash-panel">
          <h2 className="dash-panel__header">Overview</h2>
          <div className="kpi-row">
            <div className="kpi-row__item">
              <span className="kpi-row__value" data-testid="superadmin-stat-users">
                {users.length}
              </span>
              <span className="kpi-row__label">Total users</span>
            </div>
            <span className="kpi-row__divider" aria-hidden="true" />
            <div className="kpi-row__item">
              <span className="kpi-row__value" data-testid="superadmin-stat-docs">
                {totalDocsUsed}
              </span>
              <span className="kpi-row__label">Documents used (all users)</span>
            </div>
            <span className="kpi-row__divider" aria-hidden="true" />
            <div className="kpi-row__item">
              <span className="kpi-row__value kpi-row__value--accepted" data-testid="superadmin-stat-active">
                {activeCount}
              </span>
              <span className="kpi-row__label">Active</span>
            </div>
            <span className="kpi-row__divider" aria-hidden="true" />
            <div className="kpi-row__item">
              <span className="kpi-row__value kpi-row__value--rejected" data-testid="superadmin-stat-expired">
                {expiredCount}
              </span>
              <span className="kpi-row__label">Expired</span>
            </div>
          </div>
          <div className="chart-grid">
            <div className="chart-card">
              <h3>Users by status</h3>
              <DonutChart
                data={[
                  { label: "Active", value: activeCount, color: "#2a9d8f" },
                  { label: "Expired", value: expiredCount, color: "#c0392b" },
                ]}
              />
            </div>
            <div className="chart-card">
              <h3>Documents used per user</h3>
              <BarChart
                data={users.map((u) => ({ label: u.username, value: u.docs_used, color: "#0c5af7" }))}
              />
            </div>
          </div>
        </div>
      )}

      <CreateUserForm onCreated={() => setRefreshKey((k) => k + 1)} />
      <ChangePasswordForm users={users} onChanged={() => setRefreshKey((k) => k + 1)} />

      {state === "loading" && <p>Loading users…</p>}
      {state === "error" && (
        <p role="alert" data-testid="superadmin-load-error">
          {errorMessage}
        </p>
      )}
      {deleteError && (
        <p role="alert" data-testid="delete-user-error">
          {deleteError}
        </p>
      )}
      {state === "loaded" && (
        <UserTable users={users} onDelete={handleDeleteUser} deletingUsername={deletingUsername} />
      )}
    </div>
  );
}

function UserTable({
  users,
  onDelete,
  deletingUsername,
}: {
  users: UserSummaryDto[];
  onDelete: (username: string) => void;
  deletingUsername: string | null;
}) {
  if (users.length === 0) {
    return <p data-testid="superadmin-empty">No users yet.</p>;
  }
  return (
    <div className="user-table-wrap">
      <table className="user-table" data-testid="user-table">
        <thead>
          <tr>
            <th>Username</th>
            <th>Role</th>
            <th>Documents</th>
            <th>Valid until</th>
            <th>Status</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {users.map((u) => (
            <tr key={u.username}>
              <td>{u.username}</td>
              <td>{u.role}</td>
              <td>{u.doc_quota !== null ? `${u.docs_used} / ${u.doc_quota}` : `${u.docs_used} (unlimited)`}</td>
              <td>{u.valid_until ?? "never"}</td>
              <td>
                <span className={`user-status user-status--${u.status}`}>{u.status}</span>
              </td>
              <td>
                <button
                  type="button"
                  className="user-table__delete"
                  onClick={() => onDelete(u.username)}
                  disabled={deletingUsername === u.username}
                >
                  {deletingUsername === u.username ? "Deleting…" : "Delete"}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CreateUserForm({ onCreated }: { onCreated: () => void }) {
  const [username, setUsername] = useState("");
  const [role, setRole] = useState<"ap" | "admin">("ap");
  const [password, setPassword] = useState("");
  const [docQuota, setDocQuota] = useState("");
  const [validDays, setValidDays] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<{ username: string; password: string; docQuota: number | null; validUntil: string | null } | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    setCreated(null);
    try {
      const result = await createUser({
        username,
        role,
        password: password.trim() || undefined,
        doc_quota: docQuota.trim() ? Number(docQuota) : undefined,
        valid_days: validDays.trim() ? Number(validDays) : undefined,
      });
      setCreated({
        username: result.username,
        password: result.password,
        docQuota: result.doc_quota,
        validUntil: result.valid_until,
      });
      setUsername("");
      setPassword("");
      setDocQuota("");
      setValidDays("");
      onCreated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create this user.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="create-user-form">
      <h2>Create a user</h2>

      {created && (
        <div className="create-user-form__reveal" data-testid="created-user-reveal">
          <p>
            <strong>{created.username}</strong> created. Save this password now — it will
            not be shown again:
          </p>
          <p className="create-user-form__password" data-testid="created-user-password">
            {created.password}
          </p>
          <p className="create-user-form__reveal-meta">
            {created.docQuota !== null ? `${created.docQuota} document(s)` : "Unlimited documents"} ·{" "}
            {created.validUntil ? `valid until ${created.validUntil}` : "no expiry"}
          </p>
        </div>
      )}

      <form onSubmit={handleSubmit} className="create-user-form__fields">
        <label htmlFor="new-user-username">
          Username
          <input
            id="new-user-username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
          />
        </label>
        <label htmlFor="new-user-role">
          Role
          <select id="new-user-role" value={role} onChange={(e) => setRole(e.target.value as "ap" | "admin")}>
            <option value="ap">AP</option>
            <option value="admin">Admin</option>
          </select>
        </label>
        <label htmlFor="new-user-password">
          Password (leave blank to generate one)
          <input
            id="new-user-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Auto-generated if left blank"
          />
        </label>
        <label htmlFor="new-user-quota">
          Document quota (leave blank for unlimited)
          <input
            id="new-user-quota"
            type="number"
            min={0}
            value={docQuota}
            onChange={(e) => setDocQuota(e.target.value)}
            placeholder="Unlimited"
          />
        </label>
        <label htmlFor="new-user-valid-days">
          Valid for (days, leave blank for no expiry)
          <input
            id="new-user-valid-days"
            type="number"
            min={1}
            value={validDays}
            onChange={(e) => setValidDays(e.target.value)}
            placeholder="No expiry"
          />
        </label>
        {error && (
          <p role="alert" data-testid="create-user-error">
            {error}
          </p>
        )}
        <button type="submit" className="btn-primary" disabled={submitting}>
          {submitting ? "Creating…" : "Create user"}
        </button>
      </form>
    </section>
  );
}

function ChangePasswordForm({
  users,
  onChanged,
}: {
  users: UserSummaryDto[];
  onChanged: () => void;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [changed, setChanged] = useState<{ username: string; password: string } | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!username) return;
    setSubmitting(true);
    setError(null);
    setChanged(null);
    try {
      const result = await changePassword(username, password.trim() || undefined);
      setChanged(result);
      setPassword("");
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not change this password.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="create-user-form">
      <h2>Change a user's password</h2>

      {changed && (
        <div className="create-user-form__reveal" data-testid="password-changed-reveal">
          <p>
            New password for <strong>{changed.username}</strong> — save this now, it won't
            be shown again:
          </p>
          <p className="create-user-form__password" data-testid="changed-password-value">
            {changed.password}
          </p>
        </div>
      )}

      <form onSubmit={handleSubmit} className="create-user-form__fields">
        <label htmlFor="change-password-username">
          User
          <select
            id="change-password-username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
          >
            <option value="">Choose a user…</option>
            {users.map((u) => (
              <option key={u.username} value={u.username}>
                {u.username} ({u.role})
              </option>
            ))}
          </select>
        </label>
        <label htmlFor="change-password-value">
          New password (leave blank to generate one)
          <input
            id="change-password-value"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Auto-generated if left blank"
          />
        </label>
        {error && (
          <p role="alert" data-testid="change-password-error">
            {error}
          </p>
        )}
        <button type="submit" className="btn-primary" disabled={submitting || !username}>
          {submitting ? "Changing…" : "Change password"}
        </button>
      </form>
    </section>
  );
}
