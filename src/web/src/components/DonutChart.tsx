export interface ChartDatum {
  label: string;
  value: number;
  color: string;
}

/**
 * A small donut chart + legend, hand-rolled SVG (stacked circles with stroke-dasharray/
 * -dashoffset) rather than a charting library — the data here is a handful of categories
 * at most (invoice outcomes, user statuses), nowhere near what would justify the
 * dependency weight of a real charting package.
 */
export function DonutChart({
  data,
  size = 120,
  strokeWidth = 18,
}: {
  data: ChartDatum[];
  size?: number;
  strokeWidth?: number;
}) {
  const total = data.reduce((sum, d) => sum + d.value, 0);
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  let cumulative = 0;

  return (
    <div className="donut-chart">
      <svg viewBox={`0 0 ${size} ${size}`} width={size} height={size} role="img" aria-label={`${total} total`}>
        <g transform={`rotate(-90 ${size / 2} ${size / 2})`}>
          {total === 0 ? (
            <circle
              cx={size / 2}
              cy={size / 2}
              r={radius}
              fill="none"
              stroke="var(--border-strong)"
              strokeWidth={strokeWidth}
            />
          ) : (
            data
              .filter((d) => d.value > 0)
              .map((d) => {
                const dash = (d.value / total) * circumference;
                const offset = -cumulative;
                cumulative += dash;
                return (
                  <circle
                    key={d.label}
                    cx={size / 2}
                    cy={size / 2}
                    r={radius}
                    fill="none"
                    stroke={d.color}
                    strokeWidth={strokeWidth}
                    strokeDasharray={`${dash} ${circumference - dash}`}
                    strokeDashoffset={offset}
                  />
                );
              })
          )}
        </g>
        <text
          x={size / 2}
          y={size / 2}
          textAnchor="middle"
          dominantBaseline="central"
          className="donut-chart__total"
        >
          {total}
        </text>
      </svg>
      <ul className="donut-chart__legend">
        {data.map((d) => (
          <li key={d.label}>
            <span className="donut-chart__dot" style={{ background: d.color }} aria-hidden="true" />
            {d.label} ({d.value})
          </li>
        ))}
      </ul>
    </div>
  );
}
