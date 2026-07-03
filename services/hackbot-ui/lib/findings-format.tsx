// Pure helpers for the friendly Findings renderer (components/FindingsView.tsx).
// Findings have a variable schema, so these stay generic: label formatting,
// runtime type guards, and a lightweight text renderer that pretty-prints
// embedded ```json fenced blocks without any markdown dependency.

import type { ReactNode } from "react";

// Polished labels for known/acronym keys; everything else falls back to titleize.
const LABEL_OVERRIDES: Record<string, string> = {
  bug_id: "Bug ID",
  total_cost_usd: "Total Cost (USD)",
  num_turns: "Turns",
  regressor_node: "Regressor Node",
};

export function titleize(key: string): string {
  if (key in LABEL_OVERRIDES) return LABEL_OVERRIDES[key];
  return key
    .replace(/[_-]+/g, " ")
    .trim()
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .replace(/\bId\b/g, "ID")
    .replace(/\bUrl\b/g, "URL")
    .replace(/\bUsd\b/g, "USD");
}

export function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

export function isStringArray(a: unknown[]): a is string[] {
  return a.every((x) => typeof x === "string");
}

export function isScalar(v: unknown): v is string | number | boolean {
  return (
    typeof v === "string" || typeof v === "number" || typeof v === "boolean"
  );
}

// Pretty-print a fenced code block body when it is (or claims to be) JSON;
// otherwise return it verbatim.
function formatFence(lang: string, body: string): string {
  const trimmed = body.trim();
  if (lang === "json" || trimmed.startsWith("{") || trimmed.startsWith("[")) {
    try {
      return JSON.stringify(JSON.parse(trimmed), null, 2);
    } catch {
      return trimmed;
    }
  }
  return trimmed;
}

// Render a possibly-long string: prose segments keep their newlines (pre-wrap
// via .finding-text) and ```fenced``` blocks become code blocks, with JSON
// pretty-printed. Inline markdown (e.g. **bold**) is shown literally — safe by
// construction (all React text nodes, no dangerouslySetInnerHTML).
export function renderMarkdownish(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const fence = /```(\w*)\n?([\s\S]*?)```/g;
  let lastIndex = 0;
  let key = 0;
  let match: RegExpExecArray | null;

  const pushProse = (chunk: string) => {
    if (chunk.trim()) {
      nodes.push(
        <div className="finding-text" key={key++}>
          {chunk.trim()}
        </div>,
      );
    }
  };

  while ((match = fence.exec(text)) !== null) {
    pushProse(text.slice(lastIndex, match.index));
    nodes.push(
      <pre className="log" key={key++}>
        {formatFence(match[1], match[2])}
      </pre>,
    );
    lastIndex = match.index + match[0].length;
  }
  pushProse(text.slice(lastIndex));

  return nodes;
}
