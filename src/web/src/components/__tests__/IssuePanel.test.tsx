import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { IssuePanel } from "../IssuePanel";
import type { ValidationIssue } from "../../types";

function makeIssue(overrides: Partial<ValidationIssue> = {}): ValidationIssue {
  return {
    ruleId: "BR-03",
    severity: "hard",
    passed: false,
    message: "raw rule message",
    fields: ["total"],
    explanation: null,
    ...overrides,
  };
}

describe("IssuePanel", () => {
  it("shows a clean message when nothing failed", () => {
    render(<IssuePanel issues={[makeIssue({ passed: true })]} />);
    expect(screen.getByText(/No rule failures/)).toBeInTheDocument();
  });

  it("renders a failing issue's explanation when available", () => {
    render(<IssuePanel issues={[makeIssue({ explanation: "Plain-language reason." })]} />);
    expect(screen.getByText("Plain-language reason.")).toBeInTheDocument();
  });

  it("falls back to the raw rule message when no explanation is available", () => {
    // AC-14.3: explanation unavailable -> raw rule outcome shown instead, review not blocked.
    render(<IssuePanel issues={[makeIssue({ explanation: null, message: "raw rule message" })]} />);
    expect(screen.getByText("raw rule message")).toBeInTheDocument();
  });

  it("only lists failing issues, not passing ones", () => {
    render(
      <IssuePanel
        issues={[
          makeIssue({ ruleId: "BR-01", passed: true }),
          makeIssue({ ruleId: "BR-03", passed: false }),
        ]}
      />,
    );
    expect(screen.queryByText("BR-01")).not.toBeInTheDocument();
    expect(screen.getByText("BR-03")).toBeInTheDocument();
  });
});
