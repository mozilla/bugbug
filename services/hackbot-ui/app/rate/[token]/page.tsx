import type { Metadata } from "next";

import { FeedbackForm } from "@/components/FeedbackForm";
import { getFeedbackTarget } from "@/lib/hackbot";
import type { FeedbackRating } from "@/lib/types";

export const dynamic = "force-dynamic";

// Linked from public Bugzilla comments; keep them out of search indexes.
export const metadata: Metadata = {
  robots: { index: false, follow: false },
};

// Carried over from the Bugzilla link so the visitor needn't re-pick the thumb
// they already clicked. Strictly a display hint: anything else means no
// selection rather than an error, and the value is never echoed into the page.
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

  // Heading and preamble live inside FeedbackForm so submitting can replace the
  // whole panel, rather than leaving an answered question above the receipt.
  return (
    <div className="panel">
      <FeedbackForm
        token={token}
        nonce={target.nonce}
        bugId={target.bug_id}
        comment={target.comment}
        initialRating={preselect(v)}
      />
    </div>
  );
}
