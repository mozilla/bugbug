import { NextResponse } from "next/server";

import { createRun, getRun, HackbotError } from "@/lib/hackbot";
import { getAuthedEmail } from "@/lib/session";
import { isFailed } from "@/lib/types";

export const dynamic = "force-dynamic";

// POST /api/runs/:runId/retrigger: start a new run with the same inputs as a
// failed one. Inputs are read server-side, so the browser only sends a run id.
// Like POST /api/runs, the new run is attributed to the signed-in user rather
// than to the original run's requester.
export async function POST(
  _req: Request,
  { params }: { params: Promise<{ runId: string }> }
) {
  const email = await getAuthedEmail();
  if (!email) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { runId } = await params;
  try {
    const doc = await getRun(runId);
    if (!isFailed(doc.status)) {
      return NextResponse.json(
        { error: `Only failed runs can be re-run (this one is ${doc.status})` },
        { status: 409 }
      );
    }
    const run = await createRun(doc.agent, doc.inputs, email);
    return NextResponse.json(run, { status: 201 });
  } catch (err) {
    const status = err instanceof HackbotError ? err.status : 500;
    return NextResponse.json({ error: (err as Error).message }, { status });
  }
}
