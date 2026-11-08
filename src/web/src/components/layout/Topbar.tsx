import { useAuth } from "../../auth/useAuth";

export function Topbar({ onToggleNav }: { onToggleNav: () => void }) {
  const { user, logout } = useAuth();
  const initial = user?.name?.charAt(0)?.toUpperCase() ?? "?";

  return (
    <header className="app-topbar">
      <button type="button" className="app-topbar__nav-toggle" aria-label="Toggle navigation" onClick={onToggleNav}>
        ☰
      </button>
      <div className="app-topbar__search">
        <span className="app-topbar__search-icon" aria-hidden>
          ⌕
        </span>
        <input type="search" placeholder="Search invoices, vendors, or invoice IDs…" aria-label="Search" />
        <kbd>Ctrl K</kbd>
      </div>
      <div className="app-topbar__right">
        <button type="button" className="app-topbar__icon-btn" aria-label="Notifications">
          🔔
        </button>
        <div className="app-topbar__user">
          <span className="app-topbar__avatar">{initial}</span>
          <div className="app-topbar__user-text">
            <span className="app-topbar__user-name">{user?.name ?? "—"}</span>
            <span className="app-topbar__user-role">{user?.role ?? ""}</span>
          </div>
          <button type="button" className="app-topbar__logout" onClick={logout}>
            Sign out
          </button>
        </div>
      </div>
    </header>
  );
}
