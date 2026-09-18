export type UpliftSource =
  | { kind: "git"; commit: string }
  | { kind: "phabricator"; revision_id: number; diff_id?: number };

export type ParsedSources =
  | { sources: UpliftSource[]; error?: undefined }
  | { sources?: undefined; error: string };

const SHAPE_HINT =
  'each source must be {"kind": "git", "commit": "<sha>"} or ' +
  '{"kind": "phabricator", "revision_id": 12345}';

function positiveInteger(value: unknown): boolean {
  return typeof value === "number" && Number.isInteger(value) && value > 0;
}

function validateSource(entry: unknown, index: number): string | null {
  const position = `source ${index + 1}`;
  if (typeof entry !== "object" || entry === null || Array.isArray(entry)) {
    return `${position} must be an object — ${SHAPE_HINT}.`;
  }
  const source = entry as Record<string, unknown>;

  if (source.kind === "git") {
    return typeof source.commit === "string" && source.commit.trim().length > 0
      ? null
      : `${position} needs a "commit" SHA.`;
  }

  if (source.kind === "phabricator") {
    if (!positiveInteger(source.revision_id)) {
      return `${position} needs a numeric "revision_id" (the D-number).`;
    }
    if (source.diff_id !== undefined && !positiveInteger(source.diff_id)) {
      return `${position} has a "diff_id" that is not a positive number.`;
    }
    return null;
  }

  return `${position} has an unknown "kind" — ${SHAPE_HINT}.`;
}

/**
 * Parse the uplift sources textarea into the list the API expects.
 *
 * Validated here so a malformed stack is a message next to the field rather
 * than a 422. The shapes mirror hackbot-api's `UpliftSource` union.
 */
export function parseUpliftSources(value: string): ParsedSources {
  const trimmed = value.trim();
  if (!trimmed) {
    return { error: "Enter the sources to uplift as a JSON list." };
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch {
    return { error: `Sources must be valid JSON — ${SHAPE_HINT}.` };
  }

  if (!Array.isArray(parsed)) {
    return { error: "Sources must be a JSON list, applied in the order given." };
  }
  if (parsed.length === 0) {
    return { error: "Provide at least one source to uplift." };
  }

  for (const [index, entry] of parsed.entries()) {
    const problem = validateSource(entry, index);
    if (problem) return { error: problem };
  }

  return { sources: parsed as UpliftSource[] };
}
