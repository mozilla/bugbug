import type { ArtifactRef } from "./types";

// Claude Code session transcripts, published by hackbot-runtime under
// transcripts/<session>.jsonl with subagents beside them under
// transcripts/<session>/subagents/agent-<id>.{jsonl,meta.json}.
export const TRANSCRIPTS_PREFIX = "transcripts/";

const SESSION_RE = /^transcripts\/([^/]+)\.jsonl$/;
const SUBAGENT_RE =
  /^transcripts\/[^/]+\/subagents\/agent-(.+)\.(jsonl|meta\.json)$/;

export interface TranscriptEntry {
  timestamp?: string;
  [key: string]: unknown;
}

export interface SubagentFiles {
  id: string;
  jsonl: string;
  meta: string | null;
}

export interface TranscriptFiles {
  sessions: string[];
  subagents: SubagentFiles[];
}

export function transcriptArtifacts(artifacts: ArtifactRef[]): TranscriptFiles {
  const sessions: string[] = [];
  const subagents = new Map<string, SubagentFiles>();
  for (const { name } of artifacts) {
    if (SESSION_RE.test(name)) {
      sessions.push(name);
      continue;
    }
    const m = SUBAGENT_RE.exec(name);
    if (!m) continue;
    const [, id, kind] = m;
    const files = subagents.get(id) ?? { id, jsonl: "", meta: null };
    if (kind === "jsonl") files.jsonl = name;
    else files.meta = name;
    subagents.set(id, files);
  }
  return {
    sessions: sessions.sort(),
    subagents: [...subagents.values()].filter((s) => s.jsonl),
  };
}

export function hasTranscripts(artifacts: ArtifactRef[]): boolean {
  return artifacts.some((a) => SESSION_RE.test(a.name));
}

export function parseJsonl(text: string): TranscriptEntry[] {
  return text
    .split("\n")
    .filter((line) => line.trim())
    .map((line) => JSON.parse(line) as TranscriptEntry);
}

function firstTimestamp(entries: TranscriptEntry[]): string {
  return entries.find((e) => e.timestamp)?.timestamp ?? "";
}

// One run is several sessions in a row (analysis, then fix). Chain them into
// one entry list, each session kept intact, in the order they started.
export function mergeSessions(
  sessions: TranscriptEntry[][]
): TranscriptEntry[] {
  return sessions
    .map((entries, index) => ({
      entries,
      index,
      start: firstTimestamp(entries),
    }))
    .sort((a, b) => a.start.localeCompare(b.start) || a.index - b.index)
    .flatMap((s) => s.entries);
}
