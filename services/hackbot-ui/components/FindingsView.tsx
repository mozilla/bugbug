"use client";

import { useState } from "react";

import {
  isPlainObject,
  isScalar,
  isStringArray,
  titleize,
} from "@/lib/findings-format";
import { Markdown } from "./Markdown";
import { parseTestPlan, TestPlanView } from "./TestPlanView";

type ViewMode = "test-plan" | "friendly" | "raw";

const BUGZILLA_URL = "https://bugzilla.mozilla.org/show_bug.cgi?id=";
const MAX_DEPTH = 6;
// Strings shorter than this and without newlines render inline; longer ones get
// their own text block.
const INLINE_STRING_MAX = 80;

function BoolBadge({ value }: { value: boolean }) {
  return (
    <span className={`badge ${value ? "bool-true" : "bool-false"}`}>
      {value ? "yes" : "no"}
    </span>
  );
}

function ConfidenceBadge({ value }: { value: string }) {
  const level = value.toLowerCase();
  const cls =
    level === "high" || level === "medium" || level === "low"
      ? `conf-${level}`
      : "";
  return <span className={`badge ${cls}`.trim()}>{value}</span>;
}

function StringValue({ value }: { value: string }) {
  const trimmed = value.trim();
  if (!trimmed) return <span className="muted">—</span>;
  if (!trimmed.includes("\n") && trimmed.length <= INLINE_STRING_MAX) {
    return <span>{trimmed}</span>;
  }
  return <Markdown text={value} />;
}

function StringChips({ items }: { items: string[] }) {
  return (
    <ul className="chips">
      {items.map((item, i) => (
        <li className="chip" key={i}>
          {item}
        </li>
      ))}
    </ul>
  );
}

// Recursive dispatcher: narrows `unknown` at runtime and renders each value
// type in a friendly way. Handles arbitrary/unknown keys generically; a few
// keys (bug_id, confidence) get extra polish.
function FindingValue({
  value,
  fieldKey,
  depth,
}: {
  value: unknown;
  fieldKey?: string;
  depth: number;
}) {
  if (value === null || value === undefined) {
    return <span className="muted">—</span>;
  }
  if (typeof value === "boolean") {
    return <BoolBadge value={value} />;
  }
  if (typeof value === "number") {
    if (fieldKey === "bug_id") {
      return (
        <a href={`${BUGZILLA_URL}${value}`} target="_blank" rel="noreferrer">
          {value}
        </a>
      );
    }
    return <span>{value}</span>;
  }
  if (typeof value === "string") {
    if (fieldKey === "confidence" && value.trim()) {
      return <ConfidenceBadge value={value} />;
    }
    return <StringValue value={value} />;
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="muted">—</span>;
    if (isStringArray(value)) return <StringChips items={value} />;
    if (value.every(isScalar)) {
      return <StringChips items={value.map((v) => String(v))} />;
    }
    return (
      <div>
        {value.map((item, i) => (
          <div className="finding-card" key={i}>
            <div className="finding-index">#{i + 1}</div>
            <FindingValue value={item} depth={depth + 1} />
          </div>
        ))}
      </div>
    );
  }
  if (isPlainObject(value)) {
    const entries = Object.entries(value);
    if (entries.length === 0) return <span className="muted">—</span>;
    if (depth >= MAX_DEPTH) {
      return <pre className="log">{JSON.stringify(value, null, 2)}</pre>;
    }
    return (
      <dl className="kv">
        {entries.map(([k, v]) => (
          <FindingRow key={k} fieldKey={k} value={v} depth={depth + 1} />
        ))}
      </dl>
    );
  }
  return <span>{String(value)}</span>;
}

function FindingRow({
  fieldKey,
  value,
  depth,
}: {
  fieldKey: string;
  value: unknown;
  depth: number;
}) {
  return (
    <>
      <dt>{titleize(fieldKey)}</dt>
      <dd>
        <FindingValue value={value} fieldKey={fieldKey} depth={depth} />
      </dd>
    </>
  );
}

function FriendlyFindings({ findings }: { findings: Record<string, unknown> }) {
  const entries = Object.entries(findings);
  if (entries.length === 0) {
    return <p className="muted">No findings.</p>;
  }
  return (
    <dl className="kv findings-kv">
      {entries.map(([k, v]) => (
        <FindingRow key={k} fieldKey={k} value={v} depth={0} />
      ))}
    </dl>
  );
}

export function FindingsView({
  findings,
}: {
  findings: Record<string, unknown>;
}) {
  const testPlan = parseTestPlan(findings);
  // Structured test plans get a purpose built default view; all findings can
  // still be inspected through the generic friendly and raw JSON views.
  const [mode, setMode] = useState<ViewMode>(
    testPlan ? "test-plan" : "friendly"
  );
  return (
    <div className="panel">
      <div className="panel-head">
        <h2>Findings</h2>
        <div className="segmented" role="tablist" aria-label="Findings view">
          <button
            type="button"
            role="tab"
            aria-selected={mode === "raw"}
            className={mode === "raw" ? "active" : ""}
            onClick={() => setMode("raw")}
          >
            JSON
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === "friendly"}
            className={mode === "friendly" ? "active" : ""}
            onClick={() => setMode("friendly")}
          >
            Friendly
          </button>
          {testPlan && (
            <button
              type="button"
              role="tab"
              aria-selected={mode === "test-plan"}
              className={mode === "test-plan" ? "active" : ""}
              onClick={() => setMode("test-plan")}
            >
              Test Plan
            </button>
          )}
        </div>
      </div>
      {mode === "test-plan" && testPlan ? (
        <TestPlanView testPlan={testPlan} />
      ) : mode === "friendly" ? (
        <FriendlyFindings findings={findings} />
      ) : (
        <pre className="log">{JSON.stringify(findings, null, 2)}</pre>
      )}
    </div>
  );
}
