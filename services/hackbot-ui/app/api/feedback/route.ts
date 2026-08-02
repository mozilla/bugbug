import { NextResponse } from "next/server";

import { HackbotError, listFeedback } from "@/lib/hackbot";
import { getAuthedEmail } from "@/lib/session";

export const dynamic = "force-dynamic";

// GET /api/feedback — every recorded rating, for the internal review page.
// SSO-gated, unlike the public /api/rate/:token write route: this returns run
// ids, agents and raw rater comments across every bug.
export async function GET(req: Request) {
  if (!(await getAuthedEmail())) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const url = new URL(req.url);
  const rating = url.searchParams.get("rating");
  const limit = Number(url.searchParams.get("limit"));
  const offset = Number(url.searchParams.get("offset"));
  try {
    return NextResponse.json(
      await listFeedback({
        agent: url.searchParams.get("agent") ?? undefined,
        rating: rating === "up" || rating === "down" ? rating : undefined,
        runId: url.searchParams.get("run_id") ?? undefined,
        limit: Number.isFinite(limit) && limit > 0 ? limit : undefined,
        offset: Number.isFinite(offset) && offset > 0 ? offset : undefined,
      })
    );
  } catch (err) {
    const status = err instanceof HackbotError ? err.status : 500;
    return NextResponse.json({ error: (err as Error).message }, { status });
  }
}
