import type { ValidationIssue } from "../types";

interface IssuePanelProps {
  issues: ValidationIssue[];
}

/**
 * AC-14.1: plain-language explanation of any rule/match failure. AC-14.3: if the
 * explainer is unavailable, the raw rule outcome renders instead and review is never
 * blocked — that's the `issue.explanation ?? issue.message` fallback below, not a
 * separate error state.
 */
export function IssuePanel({ issues }: IssuePanelProps) {
  const failing = issues.filter((issue) => !issue.passed);

  if (failing.length === 0) {
    return <p className="issue-panel issue-panel--clean">No rule failures on this invoice.</p>;
  }

  return (
    <div role="list" aria-label="Validation issues" className="issue-panel">
      {failing.map((issue) => (
        <div key={issue.ruleId} role="listitem" className={`issue issue--${issue.severity}`}>
          <strong>{issue.ruleId}</strong>
          <p>{issue.explanation ?? issue.message}</p>
        </div>
      ))}
    </div>
  );
}
