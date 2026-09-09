"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { updateRunStatus } from "@/lib/store";
import {
  isFailed,
  isTerminal,
  type RunAction,
  type RunDoc,
  type RunRef,
} from "@/lib/types";
import { FindingsView } from "./FindingsView";
import { Markdown } from "./Markdown";
import { PATCH_ARTIFACT, PatchView } from "./PatchView";
import { StatusBadge } from "./StatusBadge";
import { parseTestPlan, TestPlanView } from "./TestPlanView";

// What a proposed action would write, rendered under its row so the reviewer
// approves the actual text and not just an action type. A comment carries its
// body in params.text; a Phabricator submission carries the title and summary of
// the revision it would open for the patch (previewed by PatchView).
function actionPreview(a: RunAction): { label: string; text: string } | null {
  const text = (v: unknown): string =>
    typeof v === "string" && v.trim() ? v : "";
  if (a.type === "bugzilla.add_comment") {
    const body = text(a.params?.text);
    return body ? { label: "Comment preview", text: body } : null;
  }
  if (a.type === "phabricator.submit_patch") {
    const body = [text(a.params?.title), text(a.params?.summary)]
      .filter(Boolean)
      .join("\n\n");
    return body ? { label: "Revision preview", text: body } : null;
  }
  return null;
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
  const router = useRouter();
  const [run, setRun] = useState<RunDoc | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [polling, setPolling] = useState(true);
  const [actions, setActions] = useState<RunAction[] | null>(null);
  const [applying, setApplying] = useState(false);
  const [applyError, setApplyError] = useState<string | null>(null);
  const [retriggering, setRetriggering] = useState(false);
  const [retriggerError, setRetriggerError] = useState<string | null>(null);
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
      updateRunStatus(runId, doc.status);
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

  const retrigger = useCallback(async () => {
    setRetriggering(true);
    setRetriggerError(null);
    try {
      const res = await fetch(`/api/runs/${runId}/retrigger`, {
        method: "POST",
      });
      const body = await res.json();
      if (!res.ok)
        throw new Error(body?.error ?? `Request failed (${res.status})`);
      // Left disabled through the navigation; the remount clears it.
      router.push(`/runs/${(body as RunRef).run_id}`);
    } catch (err) {
      setRetriggerError((err as Error).message);
      setRetriggering(false);
    }
  }, [runId, router]);

  if (!run && error) {
    return <div className="error-banner">{error}</div>;
  }
  if (!run) {
    return <p className="muted">Loading run…</p>;
  }

  const log = extractLog(run);
  const findings = run.summary?.findings ?? {};
  const hasFindings = Object.keys(findings).length > 0;
  // The QA agent gets its own purpose-built view; its plan lives on the
  // TestRail action, so findings are usually empty (raw data is in summary.json).
  const testPlan =
    run.agent === "test-plan-generator"
      ? parseTestPlan(findings, actions)
      : null;

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

  const hasPatch = run.artifacts.some((a) => a.name === PATCH_ARTIFACT);

  const canRetrigger = isFailed(run.status);
  const retriggerLabel = retriggering
    ? "Currently retriggering"
    : canRetrigger
      ? "Retrigger with same inputs"
      : run.status === "succeeded"
        ? "Run succeeded, cannot retrigger"
        : "Run in progress, cannot retrigger";

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
      {retriggerError && (
        <div className="error-banner">Retrigger failed: {retriggerError}</div>
      )}

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
        <button
          type="button"
          className="secondary"
          style={{ marginTop: 12 }}
          onClick={retrigger}
          disabled={retriggering || !canRetrigger}
        >
          {retriggerLabel}
        </button>
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

      {testPlan ? (
        <TestPlanView testPlan={testPlan} />
      ) : (
        hasFindings && <FindingsView findings={findings} />
      )}

      {hasPatch && <PatchView runId={run.run_id} />}

      {actions && actions.length > 0 && (
        <div className="panel">
          <h2>Actions ({actions.length})</h2>
          {applyError && <div className="error-banner">{applyError}</div>}
          <ul className="action-list">
            {actions.map((a) => {
              const preview = actionPreview(a);
              const url =
                typeof a.result?.url === "string" ? a.result.url : null;
              return (
                <li key={a.idx}>
                  <div className="action-row">
                    <span className={`badge ${a.status}`}>{a.status}</span>
                    <code>{a.type}</code>
                    {url && (
                      <a href={url} target="_blank" rel="noreferrer">
                        Open
                      </a>
                    )}
                    {a.error && <span className="muted">{a.error}</span>}
                  </div>
                  {preview && (
                    <div className="action-preview">
                      <span className="muted">{preview.label}</span>
                      <Markdown text={preview.text} />
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
