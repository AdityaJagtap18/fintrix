import type { ProcessingVolumePoint } from "../../api/dashboardClient";

const WIDTH = 640;
const HEIGHT = 220;
const PAD = 28;

export function ProcessingChart({ points }: { points: ProcessingVolumePoint[] }) {
  if (points.length === 0) {
    return <p className="chart-empty">No processing activity yet.</p>;
  }

  const max = Math.max(1, ...points.map((p) => p.count));
  const innerW = WIDTH - PAD * 2;
  const innerH = HEIGHT - PAD * 2;
  const stepX = points.length > 1 ? innerW / (points.length - 1) : 0;

  const coords = points.map((p, i) => {
    const x = PAD + i * stepX;
    const y = PAD + innerH - (p.count / max) * innerH;
    return [x, y] as const;
  });

  const linePath = coords.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x},${y}`).join(" ");
  const areaPath = `${linePath} L${coords[coords.length - 1][0]},${PAD + innerH} L${coords[0][0]},${PAD + innerH} Z`;

  const gridLines = [0, 0.25, 0.5, 0.75, 1];
  const labelEvery = Math.max(1, Math.floor(points.length / 6));

  return (
    <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} className="processing-chart" role="img" aria-label="Processing volume over time">
      {gridLines.map((f) => (
        <line
          key={f}
          x1={PAD}
          x2={WIDTH - PAD}
          y1={PAD + innerH * f}
          y2={PAD + innerH * f}
          stroke="var(--color-border)"
          strokeWidth={1}
        />
      ))}
      <path d={areaPath} fill="var(--color-mint)" stroke="none" />
      <path d={linePath} fill="none" stroke="var(--color-primary)" strokeWidth={2} />
      {points.map((p, i) =>
        i % labelEvery === 0 ? (
          <text
            key={p.date}
            x={coords[i][0]}
            y={HEIGHT - 6}
            fontSize={10}
            fill="var(--color-text-secondary)"
            textAnchor="middle"
          >
            {p.date.slice(5)}
          </text>
        ) : null,
      )}
    </svg>
  );
}
