"use client";

// hackbot-api has no "list runs" endpoint, so the app remembers the runs
// it has triggered in the browser's localStorage. This is intentionally
// lightweight — it is a demonstration app, not a system of record.

import type { RunStatus } from "./types";

const KEY = "hackbot-ui.runs";

export interface TrackedRun {
  run_id: string;
  agent: string;
  status: RunStatus;
  // Human-readable summary of the inputs, e.g. "bug 1846789".
  label: string;
  created_at: string;
}

export function loadRuns(): TrackedRun[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as TrackedRun[];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function saveRun(run: TrackedRun): TrackedRun[] {
  const runs = loadRuns().filter((r) => r.run_id !== run.run_id);
  runs.unshift(run);
  const trimmed = runs.slice(0, 50);
  window.localStorage.setItem(KEY, JSON.stringify(trimmed));
  return trimmed;
}

export function updateRunStatus(
  runId: string,
  status: RunStatus,
): TrackedRun[] {
  const runs = loadRuns().map((r) =>
    r.run_id === runId ? { ...r, status } : r,
  );
  window.localStorage.setItem(KEY, JSON.stringify(runs));
  return runs;
}

export function removeRun(runId: string): TrackedRun[] {
  const runs = loadRuns().filter((r) => r.run_id !== runId);
  window.localStorage.setItem(KEY, JSON.stringify(runs));
  return runs;
}
