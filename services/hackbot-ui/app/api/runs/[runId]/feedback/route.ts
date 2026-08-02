import { NextResponse } from "next/server";

import { HackbotError, submitRunFeedback } from "@/lib/hackbot";
import { getAuthedEmail } from "@/lib/session";
import { FEEDBACK_DIMENSIONS, type FeedbackDimension } from "@/lib/types";

export const dynamic = "force-dynamic";

const RATINGS = ["up", "down"] as const;
const KNOWN_DIMENSIONS = new Set<string>(
  FEEDBACK_DIMENSIONS.map((d) => d.value)
);

// POST /api/runs/:runId/feedback — a reviewer rating a run's proposed comment.
// SSO-gated, and the rating is attributed to the session's email rather than
// anything the client sends.
export async function POST(
  req: Request,
  { params }: { params: Promise<{ runId: string }> }
) {
  const email = await getAuthedEmail();
  if (!email) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { runId } = await params;
  const body = (await req.json().catch(() => null)) as Record<
    string,
    unknown
  > | null;
  const { rating, dimensions, comment } = body ?? {};

  if (!RATINGS.includes(rating as (typeof RATINGS)[number])) {
    return NextResponse.json({ error: "Invalid rating" }, { status: 400 });
  }
  const selected = Array.isArray(dimensions) ? dimensions : [];
  if (
    !selected.every((d) => typeof d === "string" && KNOWN_DIMENSIONS.has(d))
  ) {
    return NextResponse.json({ error: "Invalid dimension" }, { status: 400 });
  }

  try {
    return NextResponse.json(
      await submitRunFeedback(
        runId,
        {
          rating: rating as (typeof RATINGS)[number],
          dimensions: selected as FeedbackDimension[],
          comment:
            typeof comment === "string" && comment.trim() ? comment : null,
        },
        email
      )
    );
  } catch (err) {
    const status = err instanceof HackbotError ? err.status : 500;
    return NextResponse.json({ error: (err as Error).message }, { status });
  }
}
