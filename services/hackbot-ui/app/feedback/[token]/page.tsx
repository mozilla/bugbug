import type { Metadata } from "next";

import { FeedbackForm } from "@/components/FeedbackForm";
import { getFeedbackTarget } from "@/lib/hackbot";
import type { FeedbackRating } from "@/lib/types";

export const dynamic = "force-dynamic";

// These pages are linked from public Bugzilla comments; keep them out of search
// indexes so the long tail of crawler traffic never builds up.
export const metadata: Metadata = {
  robots: { index: false, follow: false },
};

// `?v=` is a convenience carried over from the Bugzilla link so the visitor
// doesn't have to re-pick the thumb they already clicked. It is strictly a
// display hint: anything other than up/down is treated as no selection rather
// than an error, and the value is never echoed back into the page.
function preselect(
  value: string | string[] | undefined
): FeedbackRating | null {
  return value === "up" || value === "down" ? value : null;
}

export default async function FeedbackPage({
  params,
  searchParams,
}: {
  params: Promise<{ token: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { token } = await params;
  const { v } = await searchParams;

  let target;
  try {
    target = await getFeedbackTarget(token);
  } catch {
    // Unsigned token, unknown run, or a run whose comment was never posted —
    // all indistinguishable by design.
    return (
      <div className="panel">
        <h2>Link not valid</h2>
        <p className="muted">
          This feedback link isn&apos;t valid, or the analysis it refers to is
          no longer available.
        </p>
      </div>
    );
  }

  return (
    <div className="panel">
      <h2>Was this analysis useful?</h2>
      <p className="muted">
        Hackbot posted the comment below on{" "}
        <a
          href={`https://bugzilla.mozilla.org/show_bug.cgi?id=${target.bug_id}`}
          target="_blank"
          rel="noreferrer"
        >
          bug {target.bug_id}
        </a>
        . Your rating helps us improve it. No account needed.
      </p>
      <FeedbackForm
        token={token}
        nonce={target.nonce}
        comment={target.comment}
        initialRating={preselect(v)}
      />
    </div>
  );
}
