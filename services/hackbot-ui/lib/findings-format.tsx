// Pure helpers for the friendly Findings renderer (components/FindingsView.tsx).
// Findings have a variable schema, so these stay generic: label formatting,
// runtime type guards, and JSON pretty-printing for fenced code blocks (reused
// by the shared Markdown renderer in components/Markdown.tsx).

// Polished labels for known/acronym keys; everything else falls back to titleize.
const LABEL_OVERRIDES: Record<string, string> = {
  bug_id: "Bug ID",
  total_cost_usd: "Total Cost (USD)",
  num_turns: "Turns",
  regressor_node: "Regressor Node",
  pushlog_url: "Pushlog Range",
  first_bad_changeset: "First Bad Changeset",
  last_good_changeset: "Last Good Changeset",
  regressed_by_bug: "Regressed By (Bug)",
  good_bound: "Good Bound",
  bad_bound: "Bad Bound",
  prompt_used: "Reproduction Directive",
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
// otherwise return it verbatim (trimmed).
export function formatFence(lang: string, body: string): string {
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
