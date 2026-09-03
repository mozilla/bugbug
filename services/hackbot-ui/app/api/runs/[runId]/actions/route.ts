import { NextResponse } from "next/server";

import { applyRunActions, HackbotError, listRunActions } from "@/lib/hackbot";
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
    const status = err instanceof HackbotError ? err.status : 500;
    return NextResponse.json({ error: (err as Error).message }, { status });
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
    const status = err instanceof HackbotError ? err.status : 500;
    return NextResponse.json({ error: (err as Error).message }, { status });
  }
}
