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

// Only strings this long are considered for de-duplication — short scalars
// (channel names, "blocked", booleans) legitimately repeat and must stay.
const DEDUP_MIN_LEN = 40;

// Some agents hoist a nested block up to a parent (e.g. autowebcompat-repro
// copies the primary reproduction's `steps`/`summary` onto the top-level
// `result`), so the same large text appears both at the top and inside the
// reproduction. Keep the copy that lives with its context (the most deeply
// nested occurrence) and drop the shallower hoisted copies — the data stays
// intact and together, just not repeated. Occurrences that tie at the deepest
// level (e.g. per-channel copies) are all kept.
export function dropHoistedDuplicates(
  findings: Record<string, unknown>,
): Record<string, unknown> {
  const maxDepth = new Map<string, number>();
  const scan = (value: unknown, depth: number): void => {
    if (typeof value === "string") {
      if (value.trim().length >= DEDUP_MIN_LEN) {
        maxDepth.set(value, Math.max(maxDepth.get(value) ?? -1, depth));
      }
    } else if (Array.isArray(value)) {
      value.forEach((v) => scan(v, depth + 1));
    } else if (isPlainObject(value)) {
      Object.values(value).forEach((v) => scan(v, depth + 1));
    }
  };
  scan(findings, 0);

  const walk = (value: unknown, depth: number): unknown => {
    if (Array.isArray(value)) return value.map((v) => walk(v, depth + 1));
    if (isPlainObject(value)) {
      const out: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(value)) {
        if (typeof v === "string" && v.trim().length >= DEDUP_MIN_LEN) {
          const deepest = maxDepth.get(v);
          // A deeper copy exists elsewhere → this is a hoisted duplicate; skip.
          if (deepest !== undefined && depth + 1 < deepest) continue;
        }
        out[k] = walk(v, depth + 1);
      }
      return out;
    }
    return value;
  };
  return walk(findings, 0) as Record<string, unknown>;
}

// Remove fenced code blocks that are just a JSON dump (```json ... ``` or a
// fence whose body parses as JSON). Some agents append the machine-readable
// findings as a JSON block inside a prose field (e.g. frontend-triage's
// `result`); in the human-readable view that only duplicates the structured
// fields shown separately. The raw dump remains available via the JSON toggle.
export function stripJsonFences(text: string): string {
  return text
    .replace(/```(\w*)[ \t]*\n([\s\S]*?)```/g, (whole, lang: string, body: string) => {
      const isJsonLang = (lang || "").toLowerCase() === "json";
      const trimmed = body.trim();
      const looksJson = trimmed.startsWith("{") || trimmed.startsWith("[");
      if (isJsonLang || looksJson) {
        try {
          JSON.parse(trimmed);
          return "";
        } catch {
          if (isJsonLang) return "";
        }
      }
      return whole;
    })
    .replace(/\n{3,}/g, "\n\n")
    .trim();
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
