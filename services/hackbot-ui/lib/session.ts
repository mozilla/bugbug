import "server-only";

import { headers } from "next/headers";

import { auth, isAllowedEmail } from "./auth";

// Authoritative session check used by the proxy API routes. Validates the
// session cookie (not just its presence) and re-enforces the domain allowlist.
export async function getAuthedEmail(): Promise<string | null> {
  const session = await auth.api.getSession({ headers: await headers() });
  const email = session?.user?.email ?? null;
  return isAllowedEmail(email) ? email : null;
}
