import Link from "next/link";

import { listFeedback } from "@/lib/hackbot";
import { DIMENSION_LABELS, type FeedbackDoc } from "@/lib/types";

export const dynamic = "force-dynamic";

// Internal review of everything raters have said, newest first. Guarded by the
// default SSO matcher in middleware.ts — only the /rate/* pages are public.
export default async function FeedbackPage({
  searchParams,
}: {
  searchParams: Promise<{ agent?: string; run_id?: string }>;
}) {
  const { agent, run_id: runId } = await searchParams;

  let rows: FeedbackDoc[];
  try {
    rows = await listFeedback({ agent, runId, limit: 200 });
  } catch (err) {
    return (
      <div className="panel">
        <h2>Feedback</h2>
        <p className="muted">
          Could not load feedback: {(err as Error).message}
        </p>
      </div>
    );
  }

  const up = rows.filter((r) => r.rating === "up").length;
  const agents = [...new Set(rows.map((r) => r.agent))];

  return (
    <div className="panel">
      <div className="panel-head">
        <h2>Feedback</h2>
        <span className="muted">
          👍 {up} · 👎 {rows.length - up}
        </span>
      </div>

      {agents.length > 1 && (
        <p className="muted">
          <Link href="/feedback">All agents</Link>
          {agents.map((a) => (
            <span key={a}>
              {" · "}
              <Link href={`/feedback?agent=${encodeURIComponent(a)}`}>{a}</Link>
            </span>
          ))}
        </p>
      )}

      {rows.length === 0 ? (
        <p className="muted">No ratings yet.</p>
      ) : (
        <ul className="feedback-list">
          {rows.map((row) => (
            <li key={`${row.run_id}-${row.created_at}`}>
              <div className="feedback-row">
                <span>{row.rating === "up" ? "👍" : "👎"}</span>
                <code>{row.agent}</code>
                {row.bug_id && (
                  <a
                    href={`https://bugzilla.mozilla.org/show_bug.cgi?id=${row.bug_id}`}
                    target="_blank"
                    rel="noreferrer"
                  >
                    bug {row.bug_id}
                  </a>
                )}
                <Link href={`/runs/${row.run_id}`}>run</Link>
                <span className="muted">
                  {new Date(row.created_at).toLocaleString()}
                </span>
              </div>
              {row.dimensions.length > 0 && (
                <div className="muted">
                  {row.dimensions
                    .map((d) => DIMENSION_LABELS[d] ?? d)
                    .join(" · ")}
                </div>
              )}
              {row.comment && <p>{row.comment}</p>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
