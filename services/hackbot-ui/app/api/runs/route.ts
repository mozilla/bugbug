import { NextRequest, NextResponse } from "next/server";

import { createRun, listRuns, HackbotError } from "@/lib/hackbot";
import { getAuthedEmail } from "@/lib/session";

export const dynamic = "force-dynamic";

// GET /api/runs?limit=50&offset=0&agent=<name>&status=<status>
// Returns a page of runs from hackbot-api (newest first), optionally filtered
// by agent and/or status.
export async function GET(req: NextRequest) {
  if (!(await getAuthedEmail())) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  try {
    const { searchParams } = new URL(req.url);
    const rawLimit = Number(searchParams.get("limit") ?? 50);
    const limit = Number.isFinite(rawLimit) && rawLimit > 0 ? rawLimit : 50;
    const rawOffset = Number(searchParams.get("offset") ?? 0);
    const offset = Number.isFinite(rawOffset) && rawOffset > 0 ? rawOffset : 0;
    const agent = searchParams.get("agent") || undefined;
    const status = searchParams.get("status") || undefined;
    const runs = await listRuns({ limit, offset, agent, status });
    return NextResponse.json(runs);
  } catch (err) {
    const status = err instanceof HackbotError ? err.status : 500;
    return NextResponse.json({ error: (err as Error).message }, { status });
  }
}

// POST /api/runs  { agent: string, inputs: object }
// Triggers a new agent run via hackbot-api.
export async function POST(req: NextRequest) {
  if (!(await getAuthedEmail())) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const { agent, inputs } = (body ?? {}) as {
    agent?: string;
    inputs?: Record<string, unknown>;
  };

  if (!agent || typeof agent !== "string") {
    return NextResponse.json(
      { error: "Missing 'agent' field" },
      { status: 400 }
    );
  }

  try {
    const run = await createRun(agent, inputs ?? {});
    return NextResponse.json(run, { status: 201 });
  } catch (err) {
    const status = err instanceof HackbotError ? err.status : 500;
    return NextResponse.json({ error: (err as Error).message }, { status });
  }
}
