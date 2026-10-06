import { NextResponse } from "next/server";

import { apiErrorResponse } from "@/lib/api-errors";
import { getRun } from "@/lib/hackbot";
import { getAuthedEmail } from "@/lib/session";

export const dynamic = "force-dynamic";

// GET /api/runs/:runId — proxy the full run document (state, summary, artifacts).
export async function GET(
  _req: Request,
  { params }: { params: Promise<{ runId: string }> }
) {
  if (!(await getAuthedEmail())) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { runId } = await params;
  try {
    const run = await getRun(runId);
    return NextResponse.json(run);
  } catch (err) {
    return apiErrorResponse(err);
  }
}
