import "server-only";

import { createFirefoxProfile } from "claude-profiler";

import { getArtifactDownloadUrl, HackbotError } from "./hackbot";
import {
  addSessionMarkers,
  type Profile,
  sessionSpans,
} from "./session-markers";
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
  const parsed = texts.map(parseJsonl);
  const profile = createFirefoxProfile(
    mergeSessions(parsed),
    agents
  ) as Profile;
  addSessionMarkers(profile, sessionSpans(parsed));
  // claude-profiler titles the track after the last session; the track is the
  // whole run, so label it as such.
  profile.threads[0].name = run.agent;
  profile.threads[0].processName = run.run_id;
  return profile;
}
