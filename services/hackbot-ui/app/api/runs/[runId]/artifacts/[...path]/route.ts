import { NextResponse } from "next/server";

import { getArtifactDownloadUrl, HackbotError } from "@/lib/hackbot";
import { getAuthedEmail } from "@/lib/session";

export const dynamic = "force-dynamic";

// Cap on how much of an artifact `?raw=1` reads into memory and hands to the
// browser. Enough for any patch worth previewing inline; past it, an artifact is
// a download rather than something to read in a panel.
const RAW_MAX_BYTES = 1024 * 1024;

// GET /api/runs/:runId/artifacts/*path
// Resolves a signed download URL from hackbot-api and redirects the browser
// straight to GCS, so artifact bytes never stream through this server and the
// X-API-Key stays server-side.
//
// With `?raw=1` the text is read here instead and returned as
// `{ text, truncated }`. Signed GCS URLs carry no CORS headers, so the browser
// cannot fetch one itself — an inline preview (see PatchView) has to come
// through this route.
export async function GET(
  req: Request,
  { params }: { params: Promise<{ runId: string; path: string[] }> }
) {
  if (!(await getAuthedEmail())) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { runId, path } = await params;
  const artifactName = path.join("/"); // catch-all segments are pre-decoded
  const raw = new URL(req.url).searchParams.get("raw") === "1";

  try {
    const { url } = await getArtifactDownloadUrl(runId, artifactName);
    if (!raw) {
      return NextResponse.redirect(url, 302);
    }

    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) {
      throw new HackbotError(
        `Could not read ${artifactName} (${res.status})`,
        res.status
      );
    }
    const buf = await res.arrayBuffer();
    const truncated = buf.byteLength > RAW_MAX_BYTES;
    const text = new TextDecoder().decode(buf.slice(0, RAW_MAX_BYTES));
    return NextResponse.json({ text, truncated });
  } catch (err) {
    const status = err instanceof HackbotError ? err.status : 500;
    return NextResponse.json({ error: (err as Error).message }, { status });
  }
}
