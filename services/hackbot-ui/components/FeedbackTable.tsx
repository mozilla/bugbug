"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import {
  DIMENSION_LABELS,
  type FeedbackDoc,
  type FeedbackRating,
} from "@/lib/types";

// Filtering happens here rather than server-side because the agent options must
// come from every row, not the visible ones — deriving them from the filtered
// set makes the control vanish the moment you use it. Row counts are small
// enough (a few hundred at most) that filtering in the client is also instant.
export function FeedbackTable({ rows }: { rows: FeedbackDoc[] }) {
  const [agent, setAgent] = useState("");
  const [rating, setRating] = useState<FeedbackRating | "">("");

  const agents = useMemo(
    () => [...new Set(rows.map((r) => r.agent))].sort(),
    [rows]
  );

  const visible = rows.filter(
    (r) => (!agent || r.agent === agent) && (!rating || r.rating === rating)
  );
  const up = visible.filter((r) => r.rating === "up").length;

  return (
    <>
      <div className="panel-head">
        <h2>Feedback ({visible.length})</h2>
        <div className="runs-filters">
          <button
            type="button"
            className={`thumb-filter ${rating === "up" ? "selected" : ""}`}
            aria-pressed={rating === "up"}
            title="Show only positive ratings"
            onClick={() => setRating(rating === "up" ? "" : "up")}
          >
            👍 {up}
          </button>
          <button
            type="button"
            className={`thumb-filter ${rating === "down" ? "selected" : ""}`}
            aria-pressed={rating === "down"}
            title="Show only negative ratings"
            onClick={() => setRating(rating === "down" ? "" : "down")}
          >
            👎 {visible.length - up}
          </button>
          <select
            aria-label="Filter by agent"
            value={agent}
            onChange={(e) => setAgent(e.target.value)}
          >
            <option value="">All agents</option>
            {agents.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
        </div>
      </div>

      {visible.length === 0 ? (
        <p className="muted">
          {rows.length === 0
            ? "No ratings yet."
            : "No ratings match these filters."}
        </p>
      ) : (
        <div className="table-scroll">
          <table className="runs feedback">
            <thead>
              <tr>
                <th>Rating</th>
                <th>Agent</th>
                <th>Bug</th>
                <th>Run</th>
                <th>What was wrong</th>
                <th>Comment</th>
                <th>When</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((row) => (
                <tr key={`${row.run_id}-${row.created_at}`}>
                  <td>{row.rating === "up" ? "👍" : "👎"}</td>
                  <td>{row.agent}</td>
                  <td>
                    {row.bug_id ? (
                      <a
                        href={`https://bugzilla.mozilla.org/show_bug.cgi?id=${row.bug_id}`}
                        target="_blank"
                        rel="noreferrer"
                      >
                        {row.bug_id}
                      </a>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
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
                  <td>{row.comment ?? <span className="muted">—</span>}</td>
                  <td className="muted">
                    {new Date(row.created_at).toLocaleString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
