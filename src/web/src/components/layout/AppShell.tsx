import { useState, type ReactNode } from "react";
import { AppSidebar } from "./AppSidebar";
import { Topbar } from "./Topbar";

export function AppShell({ children }: { children: ReactNode }) {
  const [navOpen, setNavOpen] = useState(false);

  return (
    <div className="app-shell">
      <AppSidebar open={navOpen} onNavigate={() => setNavOpen(false)} />
      {navOpen && (
        <button
          type="button"
          className="app-shell__backdrop"
          aria-label="Close navigation"
          onClick={() => setNavOpen(false)}
        />
      )}
      <div className="app-shell__main">
        <Topbar onToggleNav={() => setNavOpen((v) => !v)} />
        <main className="app-shell__content">{children}</main>
      </div>
    </div>
  );
}
