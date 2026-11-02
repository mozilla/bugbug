import { NextResponse } from "next/server";

import { apiErrorResponse } from "@/lib/api-errors";
import { getArtifactDownloadUrl } from "@/lib/hackbot";
import { getAuthedEmail } from "@/lib/session";

export const dynamic = "force-dynamic";

// GET /api/runs/:runId/artifacts/*path
// Resolves a signed download URL from hackbot-api and redirects the browser
// straight to GCS, so artifact bytes never stream through this server and the
// X-API-Key stays server-side.
export async function GET(
  _req: Request,
  { params }: { params: Promise<{ runId: string; path: string[] }> }
) {
  if (!(await getAuthedEmail())) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { runId, path } = await params;
  const artifactName = path.join("/"); // catch-all segments are pre-decoded

  try {
    const { url } = await getArtifactDownloadUrl(runId, artifactName);
    return NextResponse.redirect(url, 302);
  } catch (err) {
    return apiErrorResponse(err);
  }
}
