import { betterAuth } from "better-auth";
import { APIError } from "better-auth/api";

// Only Mozilla staff may use the app.
const ALLOWED_DOMAIN = "@mozilla.com";

export function isAllowedEmail(email: string | null | undefined): boolean {
  return (
    typeof email === "string" && email.toLowerCase().endsWith(ALLOWED_DOMAIN)
  );
}

export const auth = betterAuth({
  // Sign-in is exclusively via Google; no email/password.
  emailAndPassword: { enabled: false },
  // These are auto-enabled when no database is present, but we set them
  // explicitly so the stateless intent is obvious.
  session: {
    cookieCache: {
      enabled: true,
      strategy: "jwe",
      refreshCache: true,
      maxAge: 7 * 24 * 60 * 60, // 7 days
    },
  },
  account: {
    // Persist OAuth account state in a cookie instead of a database.
    storeAccountCookie: true,
  },
  socialProviders: {
    google: {
      clientId: process.env.GOOGLE_CLIENT_ID as string,
      clientSecret: process.env.GOOGLE_CLIENT_SECRET as string,
      // `hd` hints Google to preselect Mozilla accounts. It is only a hint —
      // the authoritative checks are mapProfileToUser (below) and the
      // per-request domain check in lib/session.ts.
      hd: "mozilla.com",
      // Reject any non-mozilla.com identity during the OAuth callback, before
      // a session is issued. This runs in stateless mode (unlike databaseHooks,
      // which only fire when a database adapter is configured).
      mapProfileToUser: (profile) => {
        if (!isAllowedEmail(profile.email)) {
          throw new APIError("FORBIDDEN", {
            message: "Access is restricted to @mozilla.com accounts.",
          });
        }
        return { email: profile.email };
      },
    },
  },
});
