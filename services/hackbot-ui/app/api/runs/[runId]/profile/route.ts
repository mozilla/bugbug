import { NextResponse } from "next/server";

import { apiErrorResponse } from "@/lib/api-errors";
import { getRun } from "@/lib/hackbot";
import { buildProfile } from "@/lib/profile";
import { getAuthedEmail } from "@/lib/session";

export const dynamic = "force-dynamic";

// GET /api/runs/:runId/profile — the run's Claude Code transcripts as a Firefox
// Profiler profile, built on demand from the transcripts/ artifacts.
export async function GET(
  _req: Request,
  { params }: { params: Promise<{ runId: string }> }
) {
  if (!(await getAuthedEmail())) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { runId } = await params;
  try {
    const profile = await buildProfile(await getRun(runId));
    // Transcripts are only listed once the run is finalized, so the profile is
    // stable from then on.
    return NextResponse.json(profile, {
      headers: { "Cache-Control": "private, max-age=3600" },
    });
  } catch (err) {
    return apiErrorResponse(err);
  }
}
