import { Suspense } from "react";

import { FeedbackTable } from "@/components/FeedbackTable";

export const dynamic = "force-dynamic";

// Internal review of everything raters have said. Guarded by the default SSO
// matcher in middleware.ts — only the /rate/* pages are public.
//
// The table reads its own filters from the URL and pages through the API, so
// this is just the shell; Suspense is what useSearchParams needs.
export default function FeedbackPage() {
  return (
    <div className="panel">
      <Suspense fallback={<p className="muted">Loading…</p>}>
        <FeedbackTable />
      </Suspense>
    </div>
  );
}
