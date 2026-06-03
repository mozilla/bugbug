"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { saveRun } from "@/lib/store";
import type { RunRef } from "@/lib/types";

// The demo currently exposes the bug-fix agent. The form maps directly to
// BugFixInputs in hackbot-api (bug_id required; model/max_turns/effort optional).
const AGENT = "bug-fix";

export function TriggerForm() {
  const router = useRouter();
  const [bugId, setBugId] = useState("");
  const [model, setModel] = useState("");
  const [maxTurns, setMaxTurns] = useState("");
  const [effort, setEffort] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    const parsedBugId = Number.parseInt(bugId, 10);
    if (!Number.isInteger(parsedBugId) || parsedBugId <= 0) {
      setError("Enter a valid Bugzilla bug ID.");
      return;
    }

    const inputs: Record<string, unknown> = { bug_id: parsedBugId };
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
        body: JSON.stringify({ agent: AGENT, inputs }),
      });
      const body = await res.json();
      if (!res.ok) {
        throw new Error(body?.error ?? `Request failed (${res.status})`);
      }
      const run = body as RunRef;
      saveRun({
        run_id: run.run_id,
        agent: run.agent,
        status: run.status,
        label: `bug ${parsedBugId}`,
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
        <label htmlFor="bugId">Bugzilla bug ID *</label>
        <input
          id="bugId"
          inputMode="numeric"
          placeholder="e.g. 1846789"
          value={bugId}
          onChange={(e) => setBugId(e.target.value)}
          required
        />
      </div>

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
        {submitting ? "Triggering…" : "Trigger bug-fix agent"}
      </button>
    </form>
  );
}
