"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Fragment, useCallback, useEffect, useState } from "react";

import {
  DIMENSION_LABELS,
  FEEDBACK_DIMENSIONS,
  type FeedbackDoc,
  type FeedbackRating,
  type FeedbackStats,
} from "@/lib/types";

const PAGE_SIZE = 50;

// Comments sit on their own full-width row beneath the record, the way run
// errors do in RecentRuns: they are free prose of unpredictable length, and a
// column wide enough for them would starve every other field.
const COLS = 5;

async function fetchPage(params: {
  agent?: string;
  rating?: string;
  dimension?: string;
  runId?: string;
  offset: number;
}): Promise<FeedbackDoc[] | null> {
  const qs = new URLSearchParams({
    limit: String(PAGE_SIZE),
    offset: String(params.offset),
  });
  if (params.agent) qs.set("agent", params.agent);
  if (params.rating) qs.set("rating", params.rating);
  if (params.dimension) qs.set("dimension", params.dimension);
  if (params.runId) qs.set("run_id", params.runId);
  const res = await fetch(`/api/feedback?${qs.toString()}`);
  if (!res.ok) return null;
  return (await res.json()) as FeedbackDoc[];
}

export function FeedbackTable() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const agentFilter = searchParams.get("agent") ?? "";
  const ratingFilter = searchParams.get("rating") ?? "";
  const dimensionFilter = searchParams.get("dimension") ?? "";
  const runFilter = searchParams.get("run_id") ?? "";

  const [rows, setRows] = useState<FeedbackDoc[] | null>(null);
  const [stats, setStats] = useState<FeedbackStats | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [failed, setFailed] = useState(false);

  // One unfiltered breakdown drives both the thumb counts and the agent
  // options, so neither is distorted by paging and the option list can't
  // collapse when a filter is applied.
  useEffect(() => {
    let cancelled = false;
    const qs = runFilter ? `?run_id=${encodeURIComponent(runFilter)}` : "";
    fetch(`/api/feedback/stats${qs}`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (!cancelled) setStats(data as FeedbackStats | null);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [runFilter]);

  // Counts beside each thumb track the agent filter, so the number is always
  // how many rows clicking it would show.
  const counts = agentFilter
    ? stats?.by_agent.find((a) => a.agent === agentFilter)
    : stats;
  const agentOptions = stats?.by_agent.map((a) => a.agent) ?? [];

  useEffect(() => {
    let cancelled = false;
    setRows(null);
    setFailed(false);
    fetchPage({
      agent: agentFilter || undefined,
      rating: ratingFilter || undefined,
      dimension: dimensionFilter || undefined,
      runId: runFilter || undefined,
      offset: 0,
    }).then((page) => {
      if (cancelled) return;
      if (page === null) {
        setFailed(true);
        setRows([]);
        setHasMore(false);
        return;
      }
      setRows(page);
      setHasMore(page.length === PAGE_SIZE);
    });
    return () => {
      cancelled = true;
    };
  }, [agentFilter, ratingFilter, dimensionFilter, runFilter]);

  const setFilter = useCallback(
    (key: "agent" | "rating" | "dimension", value: string) => {
      const params = new URLSearchParams(searchParams.toString());
      if (value) {
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
    if (!rows || loadingMore) return;
    setLoadingMore(true);
    const page = await fetchPage({
      agent: agentFilter || undefined,
      rating: ratingFilter || undefined,
      dimension: dimensionFilter || undefined,
      runId: runFilter || undefined,
      offset: rows.length,
    });
    // `null` means the request failed; an empty page is a valid last page.
    if (page !== null) {
      setRows((prev) => [...(prev ?? []), ...page]);
      setHasMore(page.length === PAGE_SIZE);
    }
    setLoadingMore(false);
  }, [
    rows,
    loadingMore,
    agentFilter,
    ratingFilter,
    dimensionFilter,
    runFilter,
  ]);

  function thumb(value: FeedbackRating, label: string) {
    const active = ratingFilter === value;
    const total = counts?.[value];
    return (
      <button
        type="button"
        className={`thumb-filter ${active ? "selected" : ""}`}
        aria-pressed={active}
        title={label}
        onClick={() => setFilter("rating", active ? "" : value)}
      >
        {value === "up" ? "👍" : "👎"}
        {total !== undefined && <span className="thumb-count">{total}</span>}
      </button>
    );
  }

  return (
    <>
      <div className="panel-head">
        <h2>Feedback</h2>
        <div className="runs-filters">
          {thumb("up", "Show only positive ratings")}
          {thumb("down", "Show only negative ratings")}
          <select
            aria-label="Filter by agent"
            value={agentFilter}
            onChange={(e) => setFilter("agent", e.target.value)}
          >
            <option value="">All agents</option>
            {/* A filter arriving by URL for an agent with no ratings still
                needs an option, or the select would render blank. */}
            {(agentFilter && !agentOptions.includes(agentFilter)
              ? [...agentOptions, agentFilter]
              : agentOptions
            ).map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
          {/* Unlike agents, every dimension is offered whether or not it has
              been used yet: the list is a fixed vocabulary the form always
              shows, so an empty one is a real answer rather than a dead end. */}
          <select
            aria-label="Filter by what was wrong"
            value={dimensionFilter}
            onChange={(e) => setFilter("dimension", e.target.value)}
          >
            <option value="">Anything wrong</option>
            {FEEDBACK_DIMENSIONS.map((d) => (
              <option key={d.value} value={d.value}>
                {d.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      {runFilter && (
        <p className="muted">
          Showing one run. <Link href="/feedback">Show all feedback</Link>
        </p>
      )}

      {failed && <div className="error-banner">Could not load feedback.</div>}

      {rows === null ? (
        <p className="muted">Loading…</p>
      ) : rows.length === 0 ? (
        <p className="muted">
          {agentFilter || ratingFilter || dimensionFilter
            ? "No ratings match these filters."
            : "No ratings yet."}
        </p>
      ) : (
        <>
          <div className="table-scroll">
            <table className="runs feedback">
              <thead>
                <tr>
                  <th>Rating</th>
                  <th>Agent</th>
                  <th>Run</th>
                  <th>What was wrong</th>
                  <th>When</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <Fragment key={`${row.run_id}-${row.created_at}`}>
                    <tr className={row.comment ? "has-comment" : undefined}>
                      <td>{row.rating === "up" ? "👍" : "👎"}</td>
                      <td>{row.agent}</td>
                      <td>
                        <Link href={`/runs/${row.run_id}`}>view</Link>
                      </td>
                      <td>
                        {row.dimensions.length === 0 ? (
                          <span className="muted">—</span>
                        ) : (
                          <ul className="dimension-tags">
                            {row.dimensions.map((d) => (
                              <li key={d}>{DIMENSION_LABELS[d] ?? d}</li>
                            ))}
                          </ul>
                        )}
                      </td>
                      <td className="muted">
                        {new Date(row.created_at).toLocaleString()}
                      </td>
                    </tr>
                    {row.comment && (
                      <tr className="feedback-comment-row">
                        <td colSpan={COLS}>
                          <div className="feedback-comment-text">
                            {row.comment}
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
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
