import { NextResponse } from "next/server";

import { apiErrorResponse } from "@/lib/api-errors";
import { applyRunActions, listRunActions } from "@/lib/hackbot";
import { getAuthedEmail } from "@/lib/session";

export const dynamic = "force-dynamic";

// GET /api/runs/:runId/actions — proxy the run's recorded actions + apply state.
export async function GET(
  _req: Request,
  { params }: { params: Promise<{ runId: string }> }
) {
  if (!(await getAuthedEmail())) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { runId } = await params;
  try {
    return NextResponse.json(await listRunActions(runId));
  } catch (err) {
    return apiErrorResponse(err);
  }
}

// POST /api/runs/:runId/actions — manually apply all pending actions.
export async function POST(
  _req: Request,
  { params }: { params: Promise<{ runId: string }> }
) {
  if (!(await getAuthedEmail())) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { runId } = await params;
  try {
    return NextResponse.json(await applyRunActions(runId));
  } catch (err) {
    return apiErrorResponse(err);
  }
}
