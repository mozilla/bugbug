"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { isTerminal } from "@/lib/types";
import { loadRuns, type TrackedRun, updateRunStatus } from "@/lib/store";
import { StatusBadge } from "./StatusBadge";

// Polls the status of any non-terminal tracked runs so the dashboard stays live.
const POLL_MS = 5000;

function labelFromInputs(inputs: Record<string, unknown>): string {
  if (typeof inputs.bug_id === "number") return `bug ${inputs.bug_id}`;
  return "inline report";
}

async function fetchRuns(): Promise<TrackedRun[]> {
  const res = await fetch("/api/runs?limit=50");
  if (!res.ok) return [];
  const docs = (await res.json()) as Array<{
    run_id: string;
    agent: string;
    status: TrackedRun["status"];
    inputs: Record<string, unknown>;
    created_at: string;
  }>;
  return docs.map((d) => ({
    run_id: d.run_id,
    agent: d.agent,
    status: d.status,
    label: labelFromInputs(d.inputs),
    created_at: d.created_at,
  }));
}

function mergeRuns(
  apiRuns: TrackedRun[],
  localRuns: TrackedRun[]
): TrackedRun[] {
  const seen = new Set(apiRuns.map((r) => r.run_id));
  const extras = localRuns.filter((r) => !seen.has(r.run_id));
  return [...extras, ...apiRuns];
}

export function RecentRuns() {
  const [runs, setRuns] = useState<TrackedRun[] | null>(null);

  useEffect(() => {
    fetchRuns().then((apiRuns) => {
      setRuns(mergeRuns(apiRuns, loadRuns()));
    });
  }, []);

  useEffect(() => {
    if (!runs) return;
    const active = runs.filter((r) => !isTerminal(r.status));
    if (active.length === 0) return;

    const timer = setInterval(async () => {
      let changed = false;
      for (const r of active) {
        try {
          const res = await fetch(`/api/runs/${r.run_id}`);
          if (!res.ok) continue;
          const doc = await res.json();
          if (doc.status && doc.status !== r.status) {
            updateRunStatus(r.run_id, doc.status);
            changed = true;
          }
        } catch {
          // transient; try again next tick
        }
      }
      if (changed) {
        fetchRuns().then((apiRuns) => {
          setRuns(mergeRuns(apiRuns, loadRuns()));
        });
      }
    }, POLL_MS);

    return () => clearInterval(timer);
  }, [runs]);

  if (runs === null) {
    return <p className="muted">Loading…</p>;
  }

  if (runs.length === 0) {
    return (
      <p className="muted">
        No runs yet. Use the form above to trigger an agent.
      </p>
    );
  }

  return (
    <table className="runs">
      <thead>
        <tr>
          <th>Run</th>
          <th>Agent</th>
          <th>Input</th>
          <th>Status</th>
          <th>Started</th>
        </tr>
      </thead>
      <tbody>
        {runs.map((r) => (
          <tr key={r.run_id}>
            <td className="mono">
              <Link href={`/runs/${r.run_id}`}>{r.run_id.slice(0, 8)}</Link>
            </td>
            <td>{r.agent}</td>
            <td>{r.label}</td>
            <td>
              <StatusBadge status={r.status} />
            </td>
            <td className="muted">{new Date(r.created_at).toLocaleString()}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
