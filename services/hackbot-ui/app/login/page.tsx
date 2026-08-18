"use client";

import { useState } from "react";

import { signIn } from "@/lib/auth-client";

const DEFAULT_PATH = "/";
// Browsers may strip these characters and change how the URL is parsed.
const CONTROL_CHARS = /[\u0000-\u001f\u007f]/;

function safeRedirectPath(raw: string | null): string {
  if (
    !raw ||
    !raw.startsWith("/") ||
    raw.startsWith("//") ||
    raw.includes("\\") ||
    CONTROL_CHARS.test(raw)
  ) {
    return DEFAULT_PATH;
  }

  const path = raw.split(/[?#]/)[0];
  if (path === "/login" || path.startsWith("/login/")) {
    return DEFAULT_PATH;
  }

  return raw;
}

export default function LoginPage() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onGoogle() {
    setError(null);
    setLoading(true);
    const params = new URLSearchParams(window.location.search);
    const next = safeRedirectPath(params.get("next"));

    // Keep the target across a denied sign-in so a retry still lands on it.
    const errorParams = new URLSearchParams({ error: "denied", next });

    try {
      await signIn.social({
        provider: "google",
        callbackURL: next,
        errorCallbackURL: `/login?${errorParams.toString()}`,
      });
    } catch (err) {
      setError((err as Error).message);
      setLoading(false);
    }
  }

  const denied =
    typeof window !== "undefined" &&
    new URLSearchParams(window.location.search).get("error");

  return (
    <div style={{ maxWidth: 420, margin: "64px auto" }}>
      <div className="panel">
        <h2>Sign in</h2>
        <p className="muted" style={{ marginTop: 0 }}>
          Hackbot Launchpad is restricted to <strong>@mozilla.com</strong>{" "}
          Google accounts.
        </p>
        {denied && (
          <div className="error-banner">
            Sign-in was denied. Use your @mozilla.com account.
          </div>
        )}
        {error && <div className="error-banner">{error}</div>}
        <button onClick={onGoogle} disabled={loading} style={{ width: "100%" }}>
          {loading ? "Redirecting…" : "Continue with Google"}
        </button>
      </div>
    </div>
  );
}
