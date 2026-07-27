"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Fragment, useCallback, useEffect, useState } from "react";

import { AGENT_NAMES } from "@/lib/agents";
import { useSession } from "@/lib/auth-client";
import { isTerminal, type RunDoc, type RunStatus } from "@/lib/types";
import { StatusBadge } from "./StatusBadge";

// Poll the status of any non-terminal, currently-loaded runs so the dashboard
// stays live without a full reload.
const POLL_MS = 5000;
const PAGE_SIZE = 50;
const COLS = 6;

const STATUS_OPTIONS: RunStatus[] = [
  "pending",
  "running",
  "succeeded",
  "failed",
  "timed_out",
];

// The subset of a run the table renders. Derived from RunDoc.
interface RunRow {
  run_id: string;
  agent: string;
  status: RunStatus;
  label: string;
  requestedBy: string | null;
  created_at: string;
  error: string | null;
}

// Human-readable summary of a run's inputs, mirroring the label TriggerForm
// saves at trigger time (see components/TriggerForm.tsx) so every run reads
// well straight from the API.
function labelFromInputs(inputs: Record<string, unknown>): string {
  if (typeof inputs.bug_id === "number") return `bug ${inputs.bug_id}`;
  if (typeof inputs.git_commit === "string" && inputs.git_commit.trim()) {
    return `commit ${inputs.git_commit.trim().slice(0, 12)}`;
  }
  if (typeof inputs.feature_name === "string" && inputs.feature_name.trim()) {
    return inputs.feature_name.trim();
  }
  return "inline report";
}

function toRow(d: RunDoc): RunRow {
  return {
    run_id: d.run_id,
    agent: d.agent,
    status: d.status,
    label: labelFromInputs(d.inputs),
    requestedBy: d.requested_by,
    created_at: d.created_at,
    error: d.error,
  };
}

function hasErrorDetail(r: RunRow): boolean {
  return (r.status === "failed" || r.status === "timed_out") && !!r.error;
}

// Compact requester cell: the email's local part (full email on hover). Runs
// with no requester come from automation (e.g. Phabricator webhooks).
function RequesterCell({ requestedBy }: { requestedBy: string | null }) {
  if (!requestedBy) return <span className="muted">automation</span>;
  const localPart = requestedBy.split("@")[0];
  return <span title={requestedBy}>{localPart}</span>;
}

async function fetchPage(params: {
  agent?: string;
  status?: string;
  requestedBy?: string;
  offset: number;
}): Promise<RunRow[] | null> {
  const qs = new URLSearchParams({
    limit: String(PAGE_SIZE),
    offset: String(params.offset),
  });
  if (params.agent) qs.set("agent", params.agent);
  if (params.status) qs.set("status", params.status);
  if (params.requestedBy) qs.set("requested_by", params.requestedBy);
  const res = await fetch(`/api/runs?${qs.toString()}`);
  if (!res.ok) return null;
  const docs = (await res.json()) as RunDoc[];
  return docs.map(toRow);
}

export function RecentRuns() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const { data: session, isPending: sessionPending } = useSession();
  const myEmail = session?.user?.email ?? null;

  const agentFilter = searchParams.get("agent") ?? "";
  const statusFilter = searchParams.get("status") ?? "";
  // Default to "my runs"; `?scope=all` shows everyone's.
  const showAll = searchParams.get("scope") === "all";
  const requesterFilter = showAll ? undefined : myEmail ?? undefined;
  // In "my runs" mode we need the signed-in email before we can filter, so hold
  // off fetching until the session resolves. Once resolved without an email
  // (shouldn't happen for an authed user), fall through to an unfiltered list.
  const sessionReady = showAll || myEmail !== null || !sessionPending;

  const [runs, setRuns] = useState<RunRow[] | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [failed, setFailed] = useState(false);

  // (Re)load the first page whenever the filters (or resolved identity) change.
  useEffect(() => {
    if (!sessionReady) return;
    let cancelled = false;
    setRuns(null);
    setFailed(false);
    fetchPage({
      agent: agentFilter || undefined,
      status: statusFilter || undefined,
      requestedBy: requesterFilter,
      offset: 0,
    }).then((page) => {
      if (cancelled) return;
      if (page === null) {
        setFailed(true);
        setRuns([]);
        setHasMore(false);
        return;
      }
      setRuns(page);
      setHasMore(page.length === PAGE_SIZE);
    });
    return () => {
      cancelled = true;
    };
  }, [agentFilter, statusFilter, requesterFilter, sessionReady]);

  // Live-status polling for the loaded, non-terminal runs — updated in place so
  // pagination/scroll position is preserved.
  useEffect(() => {
    if (!runs) return;
    const active = runs.filter((r) => !isTerminal(r.status));
    if (active.length === 0) return;

    const timer = setInterval(async () => {
      for (const r of active) {
        try {
          const res = await fetch(`/api/runs/${r.run_id}`);
          if (!res.ok) continue;
          const doc = (await res.json()) as RunDoc;
          if (doc.status !== r.status) {
            setRuns((prev) =>
              prev
                ? prev.map((x) =>
                    x.run_id === r.run_id
                      ? { ...x, status: doc.status, error: doc.error }
                      : x
                  )
                : prev
            );
          }
        } catch {
          // transient; retry next tick
        }
      }
    }, POLL_MS);

    return () => clearInterval(timer);
  }, [runs]);

  const setFilter = useCallback(
    (key: "agent" | "status" | "scope", value: string) => {
      const params = new URLSearchParams(searchParams.toString());
      // "mine" is the default scope, so drop the param rather than storing it.
      if (value && !(key === "scope" && value === "mine")) {
        params.set(key, value);
      } else {
        params.delete(key);
      }
      const qs = params.toString();
      router.push(qs ? `${pathname}?${qs}` : pathname);
    },
    [router, pathname, searchParams]
  );

  const loadMore = useCallback(async () => {
    if (!runs || loadingMore) return;
    setLoadingMore(true);
    const page = await fetchPage({
      agent: agentFilter || undefined,
      status: statusFilter || undefined,
      requestedBy: requesterFilter,
      offset: runs.length,
    });
    // `null` means the request failed; an empty page is a valid last page.
    if (page !== null) {
      setRuns((prev) => {
        const base = prev ?? [];
        const seen = new Set(base.map((r) => r.run_id));
        return [...base, ...page.filter((r) => !seen.has(r.run_id))];
      });
      setHasMore(page.length === PAGE_SIZE);
    }
    setLoadingMore(false);
  }, [runs, loadingMore, agentFilter, statusFilter, requesterFilter]);

  const hasFilters = Boolean(agentFilter || statusFilter || showAll);

  return (
    <>
      <div className="panel-head">
        <h2>Recent runs</h2>
        <div className="runs-filters">
          <select
            aria-label="Filter by requester"
            value={showAll ? "all" : "mine"}
            onChange={(e) => setFilter("scope", e.target.value)}
          >
            <option value="mine">My runs</option>
            <option value="all">All runs</option>
          </select>
          <select
            aria-label="Filter by agent"
            value={agentFilter}
            onChange={(e) => setFilter("agent", e.target.value)}
          >
            <option value="">All agents</option>
            {AGENT_NAMES.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
          <select
            aria-label="Filter by status"
            value={statusFilter}
            onChange={(e) => setFilter("status", e.target.value)}
          >
            <option value="">All statuses</option>
            {STATUS_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="secondary"
            onClick={() => router.push(pathname)}
            disabled={!hasFilters}
          >
            Clear
          </button>
        </div>
      </div>

      {runs === null ? (
        <p className="muted">Loading…</p>
      ) : failed ? (
        <p className="muted">Could not load runs. Try again shortly.</p>
      ) : runs.length === 0 ? (
        <p className="muted">
          {agentFilter || statusFilter
            ? "No runs match these filters."
            : showAll
              ? "No runs yet. Use the form above to trigger an agent."
              : "You haven't triggered any runs yet. Use the form above, or switch to “All runs” to see everyone's."}
        </p>
      ) : (
        <>
          <div className="table-scroll">
            <table className="runs">
              <thead>
                <tr>
                  <th>Run</th>
                  <th>Agent</th>
                  <th>Input</th>
                  <th>Requested by</th>
                  <th>Status</th>
                  <th>Started</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((r) => {
                  const showError = hasErrorDetail(r);
                  return (
                    <Fragment key={r.run_id}>
                      <tr className={showError ? "has-error" : undefined}>
                        <td className="mono">
                          <Link href={`/runs/${r.run_id}`}>
                            {r.run_id.slice(0, 8)}
                          </Link>
                        </td>
                        <td>{r.agent}</td>
                        <td>{r.label}</td>
                        <td>
                          <RequesterCell requestedBy={r.requestedBy} />
                        </td>
                        <td>
                          <StatusBadge status={r.status} />
                        </td>
                        <td className="muted">
                          {new Date(r.created_at).toLocaleString()}
                        </td>
                      </tr>
                      {showError && (
                        <tr className="run-error-row">
                          <td colSpan={COLS}>
                            <div className="run-error-text">
                              <span className="run-error-tag">Error</span>
                              {r.error}
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
          {hasMore && (
            <div className="runs-loadmore">
              <button
                type="button"
                className="secondary"
                onClick={loadMore}
                disabled={loadingMore}
              >
                {loadingMore ? "Loading…" : `Load ${PAGE_SIZE} more`}
              </button>
            </div>
          )}
        </>
      )}
    </>
  );
}
