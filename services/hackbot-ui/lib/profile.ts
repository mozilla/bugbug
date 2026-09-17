import "server-only";

import { createFirefoxProfile } from "claude-profiler";

import { getArtifactDownloadUrl, HackbotError } from "./hackbot";
import { mergeSessions, parseJsonl, transcriptArtifacts } from "./transcripts";
import type { RunDoc } from "./types";

async function fetchArtifact(runId: string, name: string): Promise<string> {
  const { url } = await getArtifactDownloadUrl(runId, name);
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) {
    throw new HackbotError(`Could not download ${name} (${res.status})`, 502);
  }
  return res.text();
}

// Build a Firefox Profiler profile from the run's Claude Code transcripts.
export async function buildProfile(run: RunDoc): Promise<object> {
  const { sessions, subagents } = transcriptArtifacts(run.artifacts);
  if (sessions.length === 0) {
    throw new HackbotError("Run has no transcripts", 404);
  }
  const [texts, agents] = await Promise.all([
    Promise.all(sessions.map((name) => fetchArtifact(run.run_id, name))),
    Promise.all(
      subagents.map(async (s) => ({
        id: s.id,
        meta: s.meta
          ? (JSON.parse(await fetchArtifact(run.run_id, s.meta)) as object)
          : {},
        entries: parseJsonl(await fetchArtifact(run.run_id, s.jsonl)),
      }))
    ),
  ]);
  return createFirefoxProfile(mergeSessions(texts.map(parseJsonl)), agents);
}
