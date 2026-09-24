import "server-only";

import * as Sentry from "@sentry/nextjs";
import { NextResponse } from "next/server";

import { HackbotError } from "./hackbot";

// Shared failure reply for the proxy route handlers. Sentry's `onRequestError`
// only sees errors that escape a handler, and these catch everything to return
// JSON, so the capture has to be explicit. Upstream 4xx is the caller's
// problem; only 5xx and unexpected throws are reported.
export function apiErrorResponse(err: unknown) {
  const status = err instanceof HackbotError ? err.status : 500;
  if (status >= 500) {
    Sentry.captureException(err);
  }
  return NextResponse.json({ error: (err as Error).message }, { status });
}
