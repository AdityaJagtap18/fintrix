import { describe, expect, it } from "vitest";
import { polygonToOverlayRect } from "../geometry";

describe("polygonToOverlayRect", () => {
  it("converts a 1-inch-square polygon at scale 1 to 72x72 px at the origin", () => {
    // (0,0) top-left to (1,1) bottom-right, one inch each side.
    const rect = polygonToOverlayRect([0, 0, 1, 0, 1, 1, 0, 1], 1);
    expect(rect).toEqual({ left: 0, top: 0, width: 72, height: 72 });
  });

  it("scales linearly with the render scale", () => {
    const rect = polygonToOverlayRect([0, 0, 1, 0, 1, 1, 0, 1], 2);
    expect(rect).toEqual({ left: 0, top: 0, width: 144, height: 144 });
  });

  it("offsets from the origin correctly", () => {
    // A box starting 2 inches from the left, 1 inch from the top, half an inch square.
    const rect = polygonToOverlayRect([2, 1, 2.5, 1, 2.5, 1.5, 2, 1.5], 1);
    expect(rect).toEqual({ left: 144, top: 72, width: 36, height: 36 });
  });

  it("handles an unrotated non-axis-ordered polygon (DI doesn't guarantee point order)", () => {
    // Same box as above, points listed in a different winding order.
    const rect = polygonToOverlayRect([2.5, 1.5, 2, 1.5, 2, 1, 2.5, 1], 1);
    expect(rect).toEqual({ left: 144, top: 72, width: 36, height: 36 });
  });

  it("bounds a slightly rotated (skewed) polygon by its axis-aligned extent", () => {
    // A slightly rotated quadrilateral — the overlay is the bounding box, not the exact
    // rotated shape (a CSS div can't easily be a rotated quad; this is a deliberate
    // simplification, not a bug).
    const rect = polygonToOverlayRect([0, 0.1, 1, 0, 0.9, 1, -0.1, 0.9], 1);
    expect(rect.left).toBeCloseTo(-0.1 * 72);
    expect(rect.top).toBeCloseTo(0);
    expect(rect.width).toBeCloseTo(1.1 * 72);
    expect(rect.height).toBeCloseTo(1 * 72);
  });

  it("rejects an odd number of coordinates", () => {
    expect(() => polygonToOverlayRect([0, 0, 1], 1)).toThrow();
  });

  it("rejects fewer than 3 points", () => {
    expect(() => polygonToOverlayRect([0, 0, 1, 1], 1)).toThrow();
  });
});
