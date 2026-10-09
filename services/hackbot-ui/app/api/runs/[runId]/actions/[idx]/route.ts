import { NextResponse } from "next/server";

import { apiErrorResponse } from "@/lib/api-errors";
import { applyRunAction } from "@/lib/hackbot";
import { getAuthedEmail } from "@/lib/session";

export const dynamic = "force-dynamic";

// POST /api/runs/:runId/actions/:idx — manually apply one action.
export async function POST(
  _req: Request,
  { params }: { params: Promise<{ runId: string; idx: string }> }
) {
  if (!(await getAuthedEmail())) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { runId, idx } = await params;
  if (!/^\d+$/.test(idx)) {
    return NextResponse.json(
      { error: "Invalid action index" },
      { status: 400 }
    );
  }
  try {
    return NextResponse.json(await applyRunAction(runId, Number(idx)));
  } catch (err) {
    return apiErrorResponse(err);
  }
}
