import Link from "next/link";

import { FeedbackTable } from "@/components/FeedbackTable";
import { listFeedback } from "@/lib/hackbot";
import type { FeedbackDoc } from "@/lib/types";

export const dynamic = "force-dynamic";

// Internal review of everything raters have said. Guarded by the default SSO
// matcher in middleware.ts — only the /rate/* pages are public.
//
// `run_id` narrows to a single run (linked from the run detail page); the agent
// and rating filters are applied client-side in FeedbackTable.
export default async function FeedbackPage({
  searchParams,
}: {
  searchParams: Promise<{ run_id?: string }>;
}) {
  const { run_id: runId } = await searchParams;

  let rows: FeedbackDoc[];
  try {
    rows = await listFeedback({ runId, limit: 200 });
  } catch (err) {
    return (
      <div className="panel">
        <h2>Feedback</h2>
        <div className="error-banner">
          Could not load feedback: {(err as Error).message}
        </div>
      </div>
    );
  }

  return (
    <div className="panel">
      {runId && (
        <p className="muted">
          Showing one run. <Link href="/feedback">Show all feedback</Link>
        </p>
      )}
      <FeedbackTable rows={rows} />
    </div>
  );
}
