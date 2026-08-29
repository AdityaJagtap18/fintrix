const POINTS_PER_INCH = 72;

export interface OverlayRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

/**
 * Converts a Document Intelligence bounding polygon — a flat [x1, y1, x2, y2, ...] array
 * in inches (DI's native unit for `boundingRegions`) — into a CSS-positionable rectangle
 * over a PDF.js canvas rendered at the given `scale`.
 *
 * PDF "user space" is points (1/72 inch), and PDF.js's viewport width/height at scale=1
 * are in that same unit — so a canvas rendered at `scale` places one inch at
 * `72 * scale` pixels. This is the one calculation in the whole review UI where a mistake
 * means the highlighted box points at the wrong number on the page (AC-02.2).
 */
export function polygonToOverlayRect(polygon: number[], scale: number): OverlayRect {
  if (polygon.length < 6 || polygon.length % 2 !== 0) {
    throw new Error(
      `polygon must have an even number of coordinates (at least 3 points), got ${polygon.length}`,
    );
  }
  const xs = polygon.filter((_, i) => i % 2 === 0);
  const ys = polygon.filter((_, i) => i % 2 === 1);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);

  const toPx = (inches: number) => inches * POINTS_PER_INCH * scale;

  return {
    left: toPx(minX),
    top: toPx(minY),
    width: toPx(maxX - minX),
    height: toPx(maxY - minY),
  };
}
