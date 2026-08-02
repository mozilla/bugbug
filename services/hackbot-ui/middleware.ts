import { getSessionCookie } from "better-auth/cookies";
import { NextRequest, NextResponse } from "next/server";

// Optimistic auth guard for the UI. This only checks for the presence of a
// valid session cookie — the proxy API routes additionally validate the
// session and the @mozilla.com allowlist server-side (see lib/session.ts).
export function middleware(req: NextRequest) {
  const sessionCookie = getSessionCookie(req);
  if (sessionCookie) {
    return NextResponse.next();
  }

  // For data routes, reply with JSON so client fetches see a clean 401 rather
  // than following a redirect to the login HTML page.
  if (req.nextUrl.pathname.startsWith("/api/")) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const loginUrl = new URL("/login", req.url);
  return NextResponse.redirect(loginUrl);
}

export const config = {
  // Everything is guarded except auth, login, static assets, and `/rate/*` —
  // the public rating pages, linked from Bugzilla comments whose readers are
  // mostly not Mozillians.
  //
  // Two cautions. Both rate entries are needed: the lookahead anchors after the
  // leading slash, so `rate` alone misses `/api/rate/*`. And exemptions are
  // prefix matches, so anything added under `/rate` becomes public silently —
  // keep that namespace to the one purpose.
  matcher: [
    "/((?!api/auth|api/rate|login|rate|_next/static|_next/image|favicon.ico).*)",
  ],
};
