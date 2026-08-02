import "server-only";

import { headers } from "next/headers";

import { auth, isAllowedEmail } from "./auth";

// Authoritative session check used by the proxy API routes. Validates the
// session cookie (not just its presence) and re-enforces the domain allowlist.
export async function getAuthedEmail(): Promise<string | null> {
  if (devUser()) return devUser();
  const session = await auth.api.getSession({ headers: await headers() });
  const email = session?.user?.email ?? null;
  return isAllowedEmail(email) ? email : null;
}

// Stands in for a Google session when running locally against sample data.
// DEV_AUTH_EMAIL belongs only in .env.local; the NODE_ENV guard makes this dead
// code in any production build.
export function devUser(): string | null {
  if (process.env.NODE_ENV === "production") return null;
  return process.env.DEV_AUTH_EMAIL || null;
}
