import { NextRequest, NextResponse } from "next/server";

import { createRun, listRuns, HackbotError } from "@/lib/hackbot";
import { getAuthedEmail } from "@/lib/session";

export const dynamic = "force-dynamic";

// GET /api/runs?limit=50&agent=bug-fix
// Returns the most recent runs from hackbot-api, optionally filtered by agent.
export async function GET(req: NextRequest) {
  if (!(await getAuthedEmail())) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  try {
    const { searchParams } = new URL(req.url);
    const raw = Number(searchParams.get("limit") ?? 50);
    const limit = Number.isFinite(raw) && raw > 0 ? raw : 50;
    const agent = searchParams.get("agent") ?? undefined;
    const runs = await listRuns(limit, agent);
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
