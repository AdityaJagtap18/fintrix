import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { BarChart } from "../BarChart";

describe("BarChart", () => {
  it("shows an empty message instead of an empty chart when there's no data", () => {
    render(<BarChart data={[]} />);

    expect(screen.getByText("No data yet.")).toBeInTheDocument();
  });

  it("draws one bar per datum, scaled relative to the largest value", () => {
    const { container } = render(
      <BarChart
        data={[
          { label: "Acme", value: 10, color: "#0c5af7" },
          { label: "Beta", value: 5, color: "#0c5af7" },
        ]}
      />,
    );

    const bars = container.querySelectorAll("rect");
    expect(bars).toHaveLength(2);
    const [tallest, shortest] = [...bars].map((r) => Number(r.getAttribute("height")));
    // The larger value's bar should be roughly twice the smaller one's, matching the 2:1
    // value ratio (10 vs 5) — not an exact pixel assertion, just the right relationship.
    expect(tallest).toBeGreaterThan(shortest);
    expect(tallest / shortest).toBeCloseTo(2, 1);
  });

  it("truncates a long label on the chart but keeps the full name in a tooltip", () => {
    const { container } = render(
      <BarChart data={[{ label: "Delta Chemicals Pvt Ltd", value: 1, color: "#0c5af7" }]} />,
    );

    expect(screen.getByText("Delta C…")).toBeInTheDocument();
    expect(container.querySelector("title")?.textContent).toBe("Delta Chemicals Pvt Ltd: 1");
  });

  it("shows a tooltip with the label and value on hover, and hides it again on mouse-leave", () => {
    const { container } = render(
      <BarChart
        data={[
          { label: "Acme", value: 10, color: "#0c5af7" },
          { label: "Beta", value: 5, color: "#0c5af7" },
        ]}
      />,
    );

    expect(screen.queryByTestId("bar-chart-tooltip")).not.toBeInTheDocument();

    const [firstBar, secondBar] = container.querySelectorAll("rect");
    fireEvent.mouseEnter(firstBar.closest("g")!);

    expect(screen.getByTestId("bar-chart-tooltip")).toHaveTextContent("Acme: 10");

    fireEvent.mouseEnter(secondBar.closest("g")!);
    expect(screen.getByTestId("bar-chart-tooltip")).toHaveTextContent("Beta: 5");

    fireEvent.mouseLeave(secondBar.closest("g")!);
    expect(screen.queryByTestId("bar-chart-tooltip")).not.toBeInTheDocument();
  });

  it("gives a zero-value bar a small visible sliver instead of disappearing entirely", () => {
    const { container } = render(
      <BarChart
        data={[
          { label: "Has data", value: 10, color: "#0c5af7" },
          { label: "No data", value: 0, color: "#0c5af7" },
        ]}
      />,
    );

    const heights = [...container.querySelectorAll("rect")].map((r) => Number(r.getAttribute("height")));
    expect(heights[1]).toBeGreaterThan(0);
  });
});
