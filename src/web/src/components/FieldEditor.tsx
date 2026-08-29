import type { ExtractedField } from "../types";

interface FieldEditorProps {
  fields: ExtractedField[];
  selectedFieldName: string | null;
  onSelectField: (name: string) => void;
  onChangeValue: (name: string, value: string) => void;
  onAcceptSuggestion: (name: string) => void;
}

/**
 * AC-02.1: only fields flagged `needsReview` are editable; every other field is visible
 * but read-only. AC-02.3: a rule-suggested value is offered as a single click.
 */
export function FieldEditor({
  fields,
  selectedFieldName,
  onSelectField,
  onChangeValue,
  onAcceptSuggestion,
}: FieldEditorProps) {
  return (
    <div role="list" aria-label="Extracted fields" className="field-editor">
      {fields.map((field) => (
        <FieldRow
          key={field.name}
          field={field}
          selected={field.name === selectedFieldName}
          onSelect={() => onSelectField(field.name)}
          onChange={(value) => onChangeValue(field.name, value)}
          onAccept={() => onAcceptSuggestion(field.name)}
        />
      ))}
    </div>
  );
}

interface FieldRowProps {
  field: ExtractedField;
  selected: boolean;
  onSelect: () => void;
  onChange: (value: string) => void;
  onAccept: () => void;
}

function FieldRow({ field, selected, onSelect, onChange, onAccept }: FieldRowProps) {
  const inputId = `field-${field.name}`;
  return (
    <div
      role="listitem"
      onClick={onSelect}
      data-selected={selected}
      data-needs-review={field.needsReview}
      className="field-row"
    >
      <label htmlFor={inputId}>{field.name}</label>

      {field.needsReview ? (
        <input
          id={inputId}
          value={field.value === null ? "" : String(field.value)}
          onChange={(event) => onChange(event.target.value)}
          onClick={(event) => event.stopPropagation()}
        />
      ) : (
        <span id={inputId} data-testid={`readonly-${field.name}`}>
          {field.value === null ? "—" : String(field.value)}
        </span>
      )}

      <span className="confidence" aria-label={`confidence ${Math.round(field.confidence * 100)}%`}>
        {Math.round(field.confidence * 100)}%
      </span>

      {field.needsReview && field.suggestedValue !== null && (
        <button
          type="button"
          onClick={(event) => {
            event.stopPropagation();
            onAccept();
          }}
        >
          Accept suggestion: {field.suggestedValue}
        </button>
      )}
    </div>
  );
}
