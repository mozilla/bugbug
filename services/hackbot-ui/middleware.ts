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
  // Protect everything except the auth endpoints, the login page, and static
  // assets.
  matcher: ["/((?!api/auth|login|_next/static|_next/image|favicon.ico).*)"],
};
