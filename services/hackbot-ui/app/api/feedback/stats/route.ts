import { NextResponse } from "next/server";

import { getFeedbackStats, HackbotError } from "@/lib/hackbot";
import { getAuthedEmail } from "@/lib/session";

export const dynamic = "force-dynamic";

// GET /api/feedback/stats — rating totals for the whole matching set, so the
// thumb filters can show counts that survive paging. SSO-gated like the list.
export async function GET(req: Request) {
  if (!(await getAuthedEmail())) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const url = new URL(req.url);
  try {
    return NextResponse.json(
      await getFeedbackStats({
        runId: url.searchParams.get("run_id") ?? undefined,
      })
    );
  } catch (err) {
    const status = err instanceof HackbotError ? err.status : 500;
    return NextResponse.json({ error: (err as Error).message }, { status });
  }
}
