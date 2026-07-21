"use client";

import { useState } from "react";

import {
  dropHoistedDuplicates,
  isPlainObject,
  isScalar,
  isStringArray,
  titleize,
} from "@/lib/findings-format";
import { Markdown } from "./Markdown";
import { parseTestPlan, TestPlanView } from "./TestPlanView";

type ViewMode = "friendly" | "raw";

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
  return <Markdown text={value} dropJsonBlocks />;
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

// A `[label, details]` pair (e.g. reproductions' `["nightly", {...}]`) reads
// better as a card headed by the label than as a nested #1/#2 pair. Returns the
// unpacked pair when `value` matches that shape, otherwise null.
function asLabeledTuple(
  value: unknown,
): { label: string; body: Record<string, unknown> } | null {
  if (
    Array.isArray(value) &&
    value.length === 2 &&
    typeof value[0] === "string" &&
    isPlainObject(value[1])
  ) {
    return { label: value[0], body: value[1] as Record<string, unknown> };
  }
  return null;
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
      <div className="finding-cards">
        {value.map((item, i) => {
          const tuple = asLabeledTuple(item);
          return (
            <div className="finding-card" key={i}>
              <div className="finding-index">{tuple ? tuple.label : `#${i + 1}`}</div>
              <FindingValue value={tuple ? tuple.body : item} depth={depth + 1} />
            </div>
          );
        })}
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

// A value that renders as a large block (a nested object, or an array of
// objects → cards) reads better as a full-width titled section than as a
// label|value row. Scalars, text, and scalar/string arrays (chips) stay rows.
function rendersAsBlock(value: unknown): boolean {
  if (isPlainObject(value)) return Object.keys(value).length > 0;
  if (Array.isArray(value)) {
    return value.length > 0 && !isStringArray(value) && !value.every(isScalar);
  }
  return false;
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
  const section = rendersAsBlock(value);
  return (
    <>
      <dt className={section ? "kv-section-title" : undefined}>
        {titleize(fieldKey)}
      </dt>
      <dd className={section ? "kv-section-body" : undefined}>
        <FindingValue value={value} fieldKey={fieldKey} depth={depth} />
      </dd>
    </>
  );
}

function FriendlyFindings({ findings }: { findings: Record<string, unknown> }) {
  // Drop hoisted duplicate text (e.g. autowebcompat-repro copies a
  // reproduction's steps/summary onto `result`); the in-context copy is kept.
  const deduped = dropHoistedDuplicates(findings);
  let entries = Object.entries(deduped);
  // When the agent emits a full `result` narrative (e.g. frontend-triage), it
  // already leads with the summary, so the separate top-level `summary` field
  // just repeats it — drop it. (Still visible via the JSON toggle.)
  const hasNarrativeResult =
    typeof deduped.result === "string" && deduped.result.trim().length > 0;
  if (hasNarrativeResult) {
    entries = entries.filter(([k]) => k !== "summary");
  }
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
  agent,
}: {
  findings: Record<string, unknown>;
  agent: string;
}) {
  const testPlan =
    agent === "test-plan-generator" ? parseTestPlan(findings) : null;

  // Default to the friendly, readable view; raw JSON is opt-in.
  const [mode, setMode] = useState<ViewMode>("friendly");
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
        </div>
      </div>
      {mode === "friendly" ? (
        testPlan ? (
          <TestPlanView testPlan={testPlan} />
        ) : (
          <FriendlyFindings findings={findings} />
        )
      ) : (
        <pre className="log">{JSON.stringify(findings, null, 2)}</pre>
      )}
    </div>
  );
}
