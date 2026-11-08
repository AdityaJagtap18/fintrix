/* Small line icons matching the feature list, drawn by hand so the project doesn't take
 * on an icon-library dependency for four glyphs. */

const common = {
  width: 24,
  height: 24,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "var(--color-primary)",
  strokeWidth: 1.8,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

export function ScanIcon() {
  return (
    <svg {...common} aria-hidden>
      <path d="M4 7V5a1 1 0 0 1 1-1h2" />
      <path d="M17 4h2a1 1 0 0 1 1 1v2" />
      <path d="M20 17v2a1 1 0 0 1-1 1h-2" />
      <path d="M7 20H5a1 1 0 0 1-1-1v-2" />
      <line x1="4" y1="12" x2="20" y2="12" />
    </svg>
  );
}

export function ShieldCheckIcon() {
  return (
    <svg {...common} aria-hidden>
      <path d="M12 3 4.5 5.5V11c0 4.5 3 7.7 7.5 9 4.5-1.3 7.5-4.5 7.5-9V5.5Z" />
      <path d="m9 12 2 2 4-4" />
    </svg>
  );
}

export function AlertIcon() {
  return (
    <svg {...common} aria-hidden>
      <path d="M12 9v4" />
      <path d="M10.3 3.9 2.5 18a1.5 1.5 0 0 0 1.3 2.2h16.4a1.5 1.5 0 0 0 1.3-2.2L13.7 3.9a1.5 1.5 0 0 0-2.6 0Z" />
      <circle cx="12" cy="16.5" r="0.6" fill="var(--color-primary)" stroke="none" />
    </svg>
  );
}

export function AnalyticsIcon() {
  return (
    <svg {...common} aria-hidden>
      <line x1="5" y1="20" x2="5" y2="13" />
      <line x1="12" y1="20" x2="12" y2="8" />
      <line x1="19" y1="20" x2="19" y2="4" />
    </svg>
  );
}
