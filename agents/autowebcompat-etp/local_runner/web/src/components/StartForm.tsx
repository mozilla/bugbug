import type { FormEvent } from "react";
import { useSnapshot } from "valtio";

import { startRun } from "../actions";
import type { AgentName } from "../api";
import { store } from "../store";
import { RunOptionsFields } from "./RunOptionsFields";

const AGENTS: { name: AgentName; label: string }[] = [
  { name: "etp", label: "ETP investigation (bug)" },
  { name: "etp_compare", label: "ETP site comparison (URL)" },
];

export function StartForm() {
  const start = store.start;
  const snap = useSnapshot(start, { sync: true });
  const compare = snap.agent === "etp_compare";

  function submit(e: FormEvent) {
    e.preventDefault();
    startRun();
  }

  return (
    <form onSubmit={submit}>
      <label>Agent</label>
      <select
        value={snap.agent}
        onChange={(e) => {
          start.agent = e.target.value as AgentName;
          start.error = null;
        }}
      >
        {AGENTS.map(({ name, label }) => (
          <option key={name} value={name}>
            {label}
          </option>
        ))}
      </select>
      {compare ? (
        <>
          <label>URL</label>
          <input
            type="url"
            placeholder="https://www.example.com/"
            value={snap.url}
            onChange={(e) => (start.url = e.target.value)}
          />
        </>
      ) : (
        <>
          <label>Bug ID</label>
          <input
            type="number"
            placeholder="e.g. 1900000"
            value={snap.bugId}
            onChange={(e) => (start.bugId = e.target.value)}
          />
          <details>
            <summary className="meta">…or paste report text instead</summary>
            <textarea
              rows={5}
              placeholder="Report text (BUG_DATA)"
              value={snap.bugData}
              onChange={(e) => (start.bugData = e.target.value)}
            />
          </details>
        </>
      )}
      <RunOptionsFields options={start.options} showMode={!compare} />
      {snap.error && <div className="note err">{snap.error}</div>}
      <button type="submit" className="wide" disabled={snap.busy}>
        Start run
      </button>
    </form>
  );
}
