import { getSessionCookie } from "better-auth/cookies";
import { NextRequest, NextResponse } from "next/server";

// Optimistic auth guard for the UI. This only checks for the presence of a
// valid session cookie — the proxy API routes additionally validate the
// session and the @mozilla.com allowlist server-side (see lib/session.ts).
export function middleware(req: NextRequest) {
  // Local development only: DEV_AUTH_EMAIL in .env.local stands in for a Google
  // session so the SSO-gated pages can be driven against sample data without
  // OAuth credentials. Mirrors devUser() in lib/session.ts, which is the
  // authoritative check — this file can't import it, since that module is
  // `server-only` and middleware runs on the edge runtime. The NODE_ENV guard
  // makes both branches dead code in any production build.
  if (process.env.NODE_ENV !== "production" && process.env.DEV_AUTH_EMAIL) {
    return NextResponse.next();
  }

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
  // Protect everything except the auth endpoints, the login page, the public
  // rating page, and static assets.
  //
  // `/rate/*` is reachable by anyone: it is linked from Bugzilla comments,
  // whose readers are mostly not Mozillians. Both entries are required — the
  // lookahead anchors immediately after the leading slash, so `rate` alone does
  // not cover `/api/rate/*`. See app/rate/[token]/page.tsx.
  //
  // Public pages live under `/rate` and nothing else does. The exemption is a
  // prefix match, so a page added under an exempted path would silently become
  // public — keeping that namespace to one purpose is what stops the internal
  // review pages under `/feedback` from being exposed by accident.
  matcher: [
    "/((?!api/auth|api/rate|login|rate|_next/static|_next/image|favicon.ico).*)",
  ],
};
