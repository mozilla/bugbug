"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { saveRun } from "@/lib/store";
import type { RunRef } from "@/lib/types";

const AGENTS = [
  { value: "bug-fix", label: "bug-fix" },
  { value: "autowebcompat-repro", label: "autowebcompat-repro" },
] as const;

type AgentValue = (typeof AGENTS)[number]["value"];

export function TriggerForm() {
  const router = useRouter();
  const [agent, setAgent] = useState<AgentValue>("bug-fix");
  const [bugId, setBugId] = useState("");
  const [bugData, setBugData] = useState("");
  const [model, setModel] = useState("");
  const [maxTurns, setMaxTurns] = useState("");
  const [effort, setEffort] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isReproAgent = agent === "autowebcompat-repro";

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    const inputs: Record<string, unknown> = {};

    const parsedBugId = bugId.trim() ? Number.parseInt(bugId, 10) : Number.NaN;
    const hasBugId = Number.isInteger(parsedBugId) && parsedBugId > 0;
    const hasBugData = isReproAgent && bugData.trim().length > 0;

    if (!isReproAgent) {
      if (!hasBugId) {
        setError("Enter a valid Bugzilla bug ID.");
        return;
      }
      inputs.bug_id = parsedBugId;
    } else {
      if (!hasBugId && !hasBugData) {
        setError("Provide a Bugzilla bug ID or paste report text.");
        return;
      }
      if (hasBugId) inputs.bug_id = parsedBugId;
      if (hasBugData) inputs.bug_data = bugData.trim();
    }

    if (model.trim()) inputs.model = model.trim();
    if (maxTurns.trim()) {
      const n = Number.parseInt(maxTurns, 10);
      if (Number.isInteger(n) && n > 0) inputs.max_turns = n;
    }
    if (effort.trim()) inputs.effort = effort.trim();

    setSubmitting(true);
    try {
      const res = await fetch("/api/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ agent, inputs }),
      });
      const body = await res.json();
      if (!res.ok) {
        throw new Error(body?.error ?? `Request failed (${res.status})`);
      }
      const run = body as RunRef;
      const label = hasBugId ? `bug ${parsedBugId}` : "inline report";
      saveRun({
        run_id: run.run_id,
        agent: run.agent,
        status: run.status,
        label,
        created_at: new Date().toISOString(),
      });
      router.push(`/runs/${run.run_id}`);
    } catch (err) {
      setError((err as Error).message);
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={onSubmit}>
      {error && <div className="error-banner">{error}</div>}

      <div className="field">
        <label htmlFor="agent">Agent</label>
        <select
          id="agent"
          value={agent}
          onChange={(e) => {
            setAgent(e.target.value as AgentValue);
            setError(null);
          }}
        >
          {AGENTS.map((a) => (
            <option key={a.value} value={a.value}>
              {a.label}
            </option>
          ))}
        </select>
      </div>

      <div className="field">
        <label htmlFor="bugId">
          {isReproAgent
            ? "Bugzilla bug ID (optional if report text provided)"
            : "Bugzilla bug ID *"}
        </label>
        <input
          id="bugId"
          inputMode="numeric"
          placeholder="e.g. 1846789"
          value={bugId}
          onChange={(e) => setBugId(e.target.value)}
          required={!isReproAgent}
        />
      </div>

      {isReproAgent && (
        <div className="field">
          <label htmlFor="bugData">
            Report text (optional if bug ID provided)
          </label>
          <textarea
            id="bugData"
            placeholder="Paste the web-compatibility report text here…"
            rows={5}
            value={bugData}
            onChange={(e) => setBugData(e.target.value)}
          />
        </div>
      )}

      <div className="row">
        <div className="field">
          <label htmlFor="model">Model (optional)</label>
          <input
            id="model"
            placeholder="default"
            value={model}
            onChange={(e) => setModel(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="maxTurns">Max turns (optional)</label>
          <input
            id="maxTurns"
            inputMode="numeric"
            placeholder="default"
            value={maxTurns}
            onChange={(e) => setMaxTurns(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="effort">Effort (optional)</label>
          <input
            id="effort"
            placeholder="default"
            value={effort}
            onChange={(e) => setEffort(e.target.value)}
          />
        </div>
      </div>

      <button type="submit" disabled={submitting}>
        {submitting ? "Triggering…" : `Trigger ${agent} agent`}
      </button>
    </form>
  );
}
