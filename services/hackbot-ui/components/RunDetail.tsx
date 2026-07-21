"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { isTerminal, type RunAction, type RunDoc } from "@/lib/types";
import { FindingsView } from "./FindingsView";
import { Markdown } from "./Markdown";
import { StatusBadge } from "./StatusBadge";

// Proposed bugzilla.add_comment actions carry the comment body in params.text;
// pull it out so we can preview what would be posted to the bug.
function commentPreview(a: RunAction): string | null {
  if (a.type !== "bugzilla.add_comment") return null;
  const text = a.params?.text;
  return typeof text === "string" && text.trim() ? text : null;
}

const POLL_MS = 4000;

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

// Link to the proxy route, which redirects to a signed download URL. Each path
// segment is encoded individually so subfolders survive the catch-all route.
function artifactHref(runId: string, name: string): string {
  const encoded = name.split("/").map(encodeURIComponent).join("/");
  return `/api/runs/${encodeURIComponent(runId)}/artifacts/${encoded}`;
}

// The agent's completion output lives in summary.findings. We surface a
// free-text "log"/"output" field as a log pane when present, and always show
// the full structured findings as JSON.
function extractLog(run: RunDoc): string | null {
  const f = run.summary?.findings;
  if (!f) return null;
  for (const key of ["log", "output", "transcript", "stdout", "message"]) {
    const v = (f as Record<string, unknown>)[key];
    if (typeof v === "string" && v.trim()) return v;
  }
  return null;
}

export function RunDetail({ runId }: { runId: string }) {
  const [run, setRun] = useState<RunDoc | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [polling, setPolling] = useState(true);
  const [actions, setActions] = useState<RunAction[] | null>(null);
  const [applying, setApplying] = useState(false);
  const [applyError, setApplyError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const fetchRun = useCallback(async () => {
    try {
      const res = await fetch(`/api/runs/${runId}`);
      const body = await res.json();
      if (!res.ok)
        throw new Error(body?.error ?? `Request failed (${res.status})`);
      const doc = body as RunDoc;
      setRun(doc);
      setError(null);
      if (isTerminal(doc.status)) {
        setPolling(false);
        return false;
      }
      return true;
    } catch (err) {
      setError((err as Error).message);
      return true; // keep retrying transient errors
    }
  }, [runId]);

  useEffect(() => {
    let cancelled = false;
    async function loop() {
      const keepGoing = await fetchRun();
      if (!cancelled && keepGoing) {
        timer.current = setTimeout(loop, POLL_MS);
      }
    }
    loop();
    return () => {
      cancelled = true;
      if (timer.current) clearTimeout(timer.current);
    };
  }, [fetchRun]);

  const fetchActions = useCallback(async () => {
    try {
      const res = await fetch(`/api/runs/${runId}/actions`);
      const body = await res.json();
      if (!res.ok)
        throw new Error(body?.error ?? `Request failed (${res.status})`);
      setActions(body as RunAction[]);
    } catch (err) {
      // Non-fatal: the actions section just stays hidden.
      setApplyError((err as Error).message);
    }
  }, [runId]);

  // Actions are recorded once the run completes; fetch them then.
  useEffect(() => {
    if (run && isTerminal(run.status)) fetchActions();
  }, [run, fetchActions]);

  const applyActions = useCallback(async () => {
    setApplying(true);
    setApplyError(null);
    try {
      const res = await fetch(`/api/runs/${runId}/actions`, { method: "POST" });
      const body = await res.json();
      if (!res.ok)
        throw new Error(body?.error ?? `Request failed (${res.status})`);
      setActions(body as RunAction[]);
    } catch (err) {
      setApplyError((err as Error).message);
    } finally {
      setApplying(false);
    }
  }, [runId]);

  if (!run && error) {
    return <div className="error-banner">{error}</div>;
  }
  if (!run) {
    return <p className="muted">Loading run…</p>;
  }

  const log = extractLog(run);
  const findings = run.summary?.findings ?? {};
  const hasFindings = Object.keys(findings).length > 0;

  // Both pending and failed actions are (re)applied by the apply endpoint — it
  // skips only already-applied ones — so one button covers applying and retry.
  const pendingActions =
    actions?.filter((a) => a.status === "pending").length ?? 0;
  const failedActions =
    actions?.filter((a) => a.status === "failed").length ?? 0;
  const applyLabel =
    pendingActions && failedActions
      ? "Apply pending & retry failed actions"
      : failedActions
        ? "Retry failed actions"
        : "Apply pending actions";

  return (
    <>
      <div className="toolbar">
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <StatusBadge status={run.status} />
          {polling && (
            <span className="spinner-note">
              <span className="dot" /> live — refreshing every {POLL_MS / 1000}s
            </span>
          )}
        </div>
        <Link href="/" className="muted">
          ← all runs
        </Link>
      </div>

      {error && <div className="error-banner">Refresh error: {error}</div>}

      <div className="panel">
        <h2>Run</h2>
        <dl className="kv">
          <dt>Run ID</dt>
          <dd>{run.run_id}</dd>
          <dt>Agent</dt>
          <dd>{run.agent}</dd>
          <dt>Inputs</dt>
          <dd>{JSON.stringify(run.inputs)}</dd>
          <dt>Created</dt>
          <dd>{new Date(run.created_at).toLocaleString()}</dd>
          <dt>Updated</dt>
          <dd>{new Date(run.updated_at).toLocaleString()}</dd>
          {run.execution_name && (
            <>
              <dt>Execution</dt>
              <dd>{run.execution_name}</dd>
            </>
          )}
        </dl>
      </div>

      {run.error && (
        <div className="panel">
          <h2>Error</h2>
          <pre className="log">{run.error}</pre>
        </div>
      )}

      {log && (
        <div className="panel">
          <h2>Agent log</h2>
          <pre className="log">{log}</pre>
        </div>
      )}

      {hasFindings && <FindingsView findings={findings} agent={run.agent} />}

      {actions && actions.length > 0 && (
        <div className="panel">
          <h2>Actions ({actions.length})</h2>
          {applyError && <div className="error-banner">{applyError}</div>}
          <ul className="action-list">
            {actions.map((a) => {
              const preview = commentPreview(a);
              return (
                <li key={a.idx}>
                  <div className="action-row">
                    <span className={`badge ${a.status}`}>{a.status}</span>
                    <code>{a.type}</code>
                    {a.error && <span className="muted">{a.error}</span>}
                  </div>
                  {preview && (
                    <div className="action-preview">
                      <span className="muted">Comment preview</span>
                      <Markdown text={preview} />
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
          {pendingActions + failedActions > 0 && (
            <button type="button" onClick={applyActions} disabled={applying}>
              {applying ? "Applying…" : applyLabel}
            </button>
          )}
        </div>
      )}

      <div className="panel">
        <h2>Artifacts ({run.artifacts.length})</h2>
        {run.artifacts.length === 0 ? (
          <p className="muted">
            {isTerminal(run.status)
              ? "No artifacts were produced."
              : "Artifacts appear once the run completes."}
          </p>
        ) : (
          <ul className="artifact-list">
            {run.artifacts.map((a) => (
              <li key={a.name}>
                <a
                  href={artifactHref(run.run_id, a.name)}
                  target="_blank"
                  rel="noreferrer"
                >
                  {a.name}
                </a>
                <span className="muted">{formatBytes(a.size)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  );
}
