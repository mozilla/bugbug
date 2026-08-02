import { NextResponse } from "next/server";

import { HackbotError, submitFeedback } from "@/lib/hackbot";
import { FEEDBACK_DIMENSIONS, type FeedbackDimension } from "@/lib/types";

export const dynamic = "force-dynamic";

const RATINGS = ["up", "down"] as const;
const KNOWN_DIMENSIONS = new Set<string>(
  FEEDBACK_DIMENSIONS.map((d) => d.value)
);
const MAX_COMMENT = 5000;

// POST /api/rate/:token — the one route handler that deliberately omits
// getAuthedEmail(), since the raters worth hearing from are Bugzilla users
// without Mozilla accounts. The API key is still injected server-side.
//
// No GET here on purpose: mail scanners and crawlers fetch every link in a
// Bugzilla comment, and a GET that wrote would hand each of them a ballot.
export async function POST(
  req: Request,
  { params }: { params: Promise<{ token: string }> }
) {
  const { token } = await params;

  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Malformed request" }, { status: 400 });
  }

  const { rating, nonce, dimensions, comment } = (body ?? {}) as Record<
    string,
    unknown
  >;

  if (!RATINGS.includes(rating as (typeof RATINGS)[number])) {
    return NextResponse.json({ error: "Invalid rating" }, { status: 400 });
  }
  if (typeof nonce !== "string" || !nonce) {
    return NextResponse.json({ error: "Missing nonce" }, { status: 400 });
  }
  if (comment != null && typeof comment !== "string") {
    return NextResponse.json({ error: "Invalid comment" }, { status: 400 });
  }
  if (typeof comment === "string" && comment.length > MAX_COMMENT) {
    return NextResponse.json({ error: "Comment too long" }, { status: 400 });
  }

  const selected = Array.isArray(dimensions) ? dimensions : [];
  if (
    !selected.every((d) => typeof d === "string" && KNOWN_DIMENSIONS.has(d))
  ) {
    return NextResponse.json({ error: "Invalid dimension" }, { status: 400 });
  }

  // Per-browser dedupe key, minted on first submit so it applies from the very
  // first rating. IP + user agent alone would collapse colleagues behind one
  // egress IP into a single rater (see anon_id in app/feedback_links.py).
  const existingKey = req.headers
    .get("cookie")
    ?.match(/(?:^|;\s*)hb_rater=([^;]+)/)?.[1];
  const raterKey = existingKey ?? crypto.randomUUID();

  try {
    const result = await submitFeedback(
      token,
      {
        rating: rating as (typeof RATINGS)[number],
        nonce,
        dimensions: selected as FeedbackDimension[],
        comment: typeof comment === "string" && comment.trim() ? comment : null,
      },
      {
        raterKey,
        forwardedFor: req.headers.get("x-forwarded-for"),
        userAgent: req.headers.get("user-agent"),
      }
    );

    const res = NextResponse.json(result);
    if (!existingKey) {
      res.cookies.set("hb_rater", raterKey, {
        httpOnly: true,
        secure: true,
        sameSite: "lax",
        // Scoped to the route that reads it. Cookie paths are prefix-matched
        // against the request path, so "/rate" would never be sent here.
        path: "/api/rate",
        maxAge: 60 * 60 * 24 * 365,
      });
    }
    return res;
  } catch (err) {
    const status = err instanceof HackbotError ? err.status : 500;
    return NextResponse.json({ error: (err as Error).message }, { status });
  }
}
