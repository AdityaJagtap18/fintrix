import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DonutChart } from "../DonutChart";

describe("DonutChart", () => {
  it("shows the total in the center and a legend entry per segment", () => {
    render(
      <DonutChart
        data={[
          { label: "Accepted", value: 3, color: "#2a9d8f" },
          { label: "Rejected", value: 1, color: "#c0392b" },
        ]}
      />,
    );

    expect(screen.getByText("4")).toBeInTheDocument();
    expect(screen.getByText("Accepted (3)")).toBeInTheDocument();
    expect(screen.getByText("Rejected (1)")).toBeInTheDocument();
  });

  it("renders a neutral ring and a total of 0 when every value is 0", () => {
    render(
      <DonutChart
        data={[
          { label: "Accepted", value: 0, color: "#2a9d8f" },
          { label: "Rejected", value: 0, color: "#c0392b" },
        ]}
      />,
    );

    expect(screen.getByText("0")).toBeInTheDocument();
    // Zero-value segments still get a legend row — just no visible slice.
    expect(screen.getByText("Accepted (0)")).toBeInTheDocument();
  });

  it("skips a zero-value segment when drawing slices but keeps its legend entry", () => {
    const { container } = render(
      <DonutChart
        data={[
          { label: "Accepted", value: 5, color: "#2a9d8f" },
          { label: "Rejected", value: 0, color: "#c0392b" },
        ]}
      />,
    );

    // Only one <circle> segment drawn (the zero-value one is filtered out) — the total
    // isn't zero, so this isn't the neutral-ring path either.
    const circles = container.querySelectorAll("circle");
    expect(circles).toHaveLength(1);
    expect(screen.getByText("Rejected (0)")).toBeInTheDocument();
  });
});
