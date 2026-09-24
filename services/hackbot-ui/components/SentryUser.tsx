"use client";

import * as Sentry from "@sentry/nextjs";
import { useEffect } from "react";

import { useSession } from "@/lib/auth-client";

// Tags browser-side Sentry events with the signed-in user. Renders nothing.
// The server-side equivalent lives in lib/session.ts.
export function SentryUser() {
  const { data: session } = useSession();
  const email = session?.user?.email;

  useEffect(() => {
    Sentry.setUser(email ? { email } : null);
  }, [email]);

  return null;
}
