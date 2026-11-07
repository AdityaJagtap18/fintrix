import { useState } from "react";

export interface ChartDatum {
  label: string;
  value: number;
  color: string;
}

// Room above the tallest possible bar for the hover tooltip to sit in without getting
// clipped by the viewBox edge.
const TOOLTIP_MARGIN = 20;

/**
 * A small vertical bar chart, hand-rolled SVG — same reasoning as DonutChart's own
 * docstring. The on-chart label under each bar is truncated (rarely room for a whole
 * vendor name under a 28px-wide bar) — hovering a bar shows the full label and exact
 * value in a small tooltip instead of relying on the native SVG <title> (real, but too
 * slow and inconsistent across browsers to count as "hover and see something").
 */
export function BarChart({ data, height = 110 }: { data: ChartDatum[]; height?: number }) {
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null);

  if (data.length === 0) {
    return <p className="bar-chart__empty">No data yet.</p>;
  }

  const max = Math.max(1, ...data.map((d) => d.value));
  const barWidth = 28;
  const gap = 14;
  const width = data.length * (barWidth + gap) + gap;
  const chartHeight = height + 34;

  return (
    <svg
      viewBox={`0 ${-TOOLTIP_MARGIN} ${width} ${chartHeight + TOOLTIP_MARGIN}`}
      width={width}
      height={chartHeight + TOOLTIP_MARGIN}
      className="bar-chart"
      role="img"
      aria-label={`Bar chart with ${data.length} bar(s)`}
    >
      {data.map((d, i) => {
        const barHeight = Math.max(2, (d.value / max) * height);
        const x = gap + i * (barWidth + gap);
        const y = height - barHeight;
        const label = d.label.length > 8 ? `${d.label.slice(0, 7)}…` : d.label;
        const tooltipText = `${d.label}: ${d.value}`;
        // No real text-measurement pass here (that would need a ref + layout effect for
        // a tooltip this small) — a per-character width estimate at the tooltip's 9px
        // font is close enough, and erring wide avoids clipped text more than it costs
        // visual precision.
        const tooltipWidth = tooltipText.length * 5.4 + 12;
        const tooltipX = Math.min(
          Math.max(x + barWidth / 2 - tooltipWidth / 2, 2),
          width - tooltipWidth - 2,
        );
        const tooltipY = Math.max(y - 22, -TOOLTIP_MARGIN + 2);

        return (
          <g
            key={`${d.label}-${i}`}
            onMouseEnter={() => setHoveredIndex(i)}
            onMouseLeave={() => setHoveredIndex(null)}
          >
            <title>{tooltipText}</title>
            <rect x={x} y={y} width={barWidth} height={barHeight} rx={4} fill={d.color} />
            <text x={x + barWidth / 2} y={height + 18} textAnchor="middle" className="bar-chart__label">
              {label}
            </text>
            {hoveredIndex === i && (
              <g className="bar-chart__tooltip" data-testid="bar-chart-tooltip">
                <rect x={tooltipX} y={tooltipY} width={tooltipWidth} height={17} rx={4} />
                <text x={tooltipX + tooltipWidth / 2} y={tooltipY + 12} textAnchor="middle">
                  {tooltipText}
                </text>
              </g>
            )}
          </g>
        );
      })}
    </svg>
  );
}
