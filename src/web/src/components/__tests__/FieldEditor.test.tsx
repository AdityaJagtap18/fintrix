import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { FieldEditor } from "../FieldEditor";
import type { ExtractedField } from "../../types";

function makeField(overrides: Partial<ExtractedField> = {}): ExtractedField {
  return {
    name: "total",
    value: "100.00",
    confidence: 0.9,
    page: 1,
    region: null,
    source: "model",
    needsReview: false,
    suggestedValue: null,
    ...overrides,
  };
}

describe("FieldEditor", () => {
  it("renders a flagged field as an editable input", () => {
    render(
      <FieldEditor
        fields={[makeField({ needsReview: true })]}
        selectedFieldName={null}
        onSelectField={vi.fn()}
        onChangeValue={vi.fn()}
        onAcceptSuggestion={vi.fn()}
      />,
    );
    expect(screen.getByLabelText("total")).toBeInstanceOf(HTMLInputElement);
  });

  it("renders a non-flagged field as read-only text, not an input", () => {
    // AC-02.1: only fields flagged for review are editable; everything else is visible
    // but read-only.
    render(
      <FieldEditor
        fields={[makeField({ needsReview: false })]}
        selectedFieldName={null}
        onSelectField={vi.fn()}
        onChangeValue={vi.fn()}
        onAcceptSuggestion={vi.fn()}
      />,
    );
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.getByTestId("readonly-total")).toHaveTextContent("100.00");
  });

  it("calls onSelectField when a row is clicked", () => {
    const onSelectField = vi.fn();
    render(
      <FieldEditor
        fields={[makeField()]}
        selectedFieldName={null}
        onSelectField={onSelectField}
        onChangeValue={vi.fn()}
        onAcceptSuggestion={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("listitem"));
    expect(onSelectField).toHaveBeenCalledWith("total");
  });

  it("calls onChangeValue when editing a flagged field", () => {
    const onChangeValue = vi.fn();
    render(
      <FieldEditor
        fields={[makeField({ needsReview: true, value: "100.00" })]}
        selectedFieldName={null}
        onSelectField={vi.fn()}
        onChangeValue={onChangeValue}
        onAcceptSuggestion={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByLabelText("total"), { target: { value: "150.00" } });
    expect(onChangeValue).toHaveBeenCalledWith("total", "150.00");
  });

  it("shows an accept-suggestion button only when a suggestion exists on a flagged field", () => {
    render(
      <FieldEditor
        fields={[makeField({ needsReview: true, suggestedValue: "42.00" })]}
        selectedFieldName={null}
        onSelectField={vi.fn()}
        onChangeValue={vi.fn()}
        onAcceptSuggestion={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: /Accept suggestion: 42.00/ })).toBeInTheDocument();
  });

  it("clicking accept-suggestion calls onAcceptSuggestion without also selecting the row", () => {
    // AC-02.3: single click accepts the suggestion. It shouldn't also trigger row
    // selection — event propagation from the button must be stopped.
    const onAcceptSuggestion = vi.fn();
    const onSelectField = vi.fn();
    render(
      <FieldEditor
        fields={[makeField({ needsReview: true, suggestedValue: "42.00" })]}
        selectedFieldName={null}
        onSelectField={onSelectField}
        onChangeValue={vi.fn()}
        onAcceptSuggestion={onAcceptSuggestion}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Accept suggestion/ }));
    expect(onAcceptSuggestion).toHaveBeenCalledWith("total");
    expect(onSelectField).not.toHaveBeenCalled();
  });

  it("does not show an accept-suggestion button when there is no suggestion", () => {
    render(
      <FieldEditor
        fields={[makeField({ needsReview: true, suggestedValue: null })]}
        selectedFieldName={null}
        onSelectField={vi.fn()}
        onChangeValue={vi.fn()}
        onAcceptSuggestion={vi.fn()}
      />,
    );
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
