const STATUS_COLORS: Record<string, string> = {
  approved: "var(--color-primary)",
  processed: "var(--color-secondary)",
  needs_review: "#f4a94a",
  flagged: "#e26a5c",
  rejected: "#b0392f",
};

const STATUS_LABELS: Record<string, string> = {
  approved: "Approved",
  processed: "Processed",
  needs_review: "In review",
  flagged: "Flagged",
  rejected: "Rejected",
};

const SIZE = 160;
const STROKE = 22;
const RADIUS = (SIZE - STROKE) / 2;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

export function StatusChart({ breakdown }: { breakdown: Record<string, number> }) {
  const entries = Object.entries(breakdown).filter(([, count]) => count > 0);
  const total = entries.reduce((sum, [, count]) => sum + count, 0);

  if (total === 0) {
    return <p className="chart-empty">No invoices yet.</p>;
  }

  let offset = 0;
  const segments = entries.map(([status, count]) => {
    const fraction = count / total;
    const dash = fraction * CIRCUMFERENCE;
    const segment = { status, count, dash, offset };
    offset += dash;
    return segment;
  });

  return (
    <div className="status-chart">
      <svg viewBox={`0 0 ${SIZE} ${SIZE}`} width={SIZE} height={SIZE} role="img" aria-label="Status breakdown">
        <g transform={`rotate(-90 ${SIZE / 2} ${SIZE / 2})`}>
          <circle cx={SIZE / 2} cy={SIZE / 2} r={RADIUS} fill="none" stroke="var(--color-border)" strokeWidth={STROKE} />
          {segments.map((s) => (
            <circle
              key={s.status}
              cx={SIZE / 2}
              cy={SIZE / 2}
              r={RADIUS}
              fill="none"
              stroke={STATUS_COLORS[s.status] ?? "var(--color-text-secondary)"}
              strokeWidth={STROKE}
              strokeDasharray={`${s.dash} ${CIRCUMFERENCE - s.dash}`}
              strokeDashoffset={-s.offset}
            />
          ))}
        </g>
        <text x={SIZE / 2} y={SIZE / 2 - 6} textAnchor="middle" fontSize={22} fontWeight={700} fill="var(--color-text)">
          {total}
        </text>
        <text x={SIZE / 2} y={SIZE / 2 + 14} textAnchor="middle" fontSize={11} fill="var(--color-text-secondary)">
          Invoices
        </text>
      </svg>
      <ul className="status-chart__legend">
        {segments.map((s) => (
          <li key={s.status}>
            <span className="status-chart__dot" style={{ background: STATUS_COLORS[s.status] ?? "#999" }} />
            {STATUS_LABELS[s.status] ?? s.status}
            <span className="status-chart__legend-count">
              {Math.round((s.count / total) * 100)}% ({s.count})
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
