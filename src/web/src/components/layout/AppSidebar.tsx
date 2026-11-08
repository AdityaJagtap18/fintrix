import { NavLink } from "react-router-dom";
import { LogoMark } from "../common/LogoMark";

const NAV_ITEMS = [
  { to: "/", label: "Overview", end: true },
  { to: "/invoices", label: "Invoices" },
  { to: "/review", label: "Review Queue" },
  { to: "/vendors", label: "Vendors" },
  { to: "/analytics", label: "Analytics" },
  { to: "/settings", label: "Settings" },
];

export function AppSidebar({ open, onNavigate }: { open: boolean; onNavigate: () => void }) {
  return (
    <aside className={`app-sidebar${open ? " app-sidebar--open" : ""}`}>
      <div className="app-sidebar__logo">
        <LogoMark height={26} />
      </div>
      <nav className="app-sidebar__nav">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            onClick={onNavigate}
            className={({ isActive }) => `app-sidebar__link${isActive ? " app-sidebar__link--active" : ""}`}
          >
            <span>{item.label}</span>
          </NavLink>
        ))}
      </nav>
      <div className="app-sidebar__promo">
        <p className="app-sidebar__promo-title">Automate your invoice processing</p>
        <p className="app-sidebar__promo-sub">Extract · Validate · Reconcile</p>
      </div>
    </aside>
  );
}
