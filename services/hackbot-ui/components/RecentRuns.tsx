"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { parseAgent } from "@/lib/agents";
import { isTerminal, type RunStatus } from "@/lib/types";
import { StatusBadge } from "./StatusBadge";

// Polls while any run is non-terminal so the dashboard stays live.
const POLL_MS = 5000;

interface RunRow {
  run_id: string;
  agent: string;
  status: RunStatus;
  // Human-readable summary of the inputs, e.g. "bug 1846789".
  label: string;
  created_at: string;
}

function labelFromInputs(inputs: Record<string, unknown>): string {
  if (typeof inputs.bug_id === "number") return `bug ${inputs.bug_id}`;
  if (typeof inputs.git_commit === "string") {
    return `commit ${inputs.git_commit.slice(0, 12)}`;
  }
  if (typeof inputs.feature_name === "string") return inputs.feature_name;
  return "inline report";
}

// The runs list is sourced entirely from hackbot-api (no localStorage), filtered
// server-side to the agent selected in the trigger form (carried in `?agent=`).
async function fetchRuns(agent: string): Promise<RunRow[]> {
  const params = new URLSearchParams({ limit: "50", agent });
  const res = await fetch(`/api/runs?${params.toString()}`);
  if (!res.ok) return [];
  const docs = (await res.json()) as Array<{
    run_id: string;
    agent: string;
    status: RunStatus;
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

export function RecentRuns() {
  const params = useSearchParams();
  const agent = parseAgent(params.get("agent"));
  const [runs, setRuns] = useState<RunRow[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    setRuns(null);
    fetchRuns(agent).then((rows) => {
      if (!cancelled) setRuns(rows);
    });
    return () => {
      cancelled = true;
    };
  }, [agent]);

  useEffect(() => {
    if (!runs) return;
    if (!runs.some((r) => !isTerminal(r.status))) return;

    const timer = setInterval(async () => {
      const rows = await fetchRuns(agent);
      setRuns(rows);
    }, POLL_MS);

    return () => clearInterval(timer);
  }, [runs, agent]);

  if (runs === null) {
    return <p className="muted">Loading…</p>;
  }

  if (runs.length === 0) {
    return (
      <p className="muted">
        No runs yet for the {agent} agent. Use the form above to trigger one.
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
