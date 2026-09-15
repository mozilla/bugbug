import { NextResponse } from "next/server";

import { apiErrorResponse } from "@/lib/api-errors";
import { listAgents } from "@/lib/hackbot";
import { getAuthedEmail } from "@/lib/session";

export const dynamic = "force-dynamic";

export async function GET() {
  if (!(await getAuthedEmail())) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  try {
    const agents = await listAgents();
    return NextResponse.json(agents);
  } catch (err) {
    return apiErrorResponse(err);
  }
}
