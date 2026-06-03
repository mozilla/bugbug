"use client";

import { useState } from "react";

import { signIn } from "@/lib/auth-client";

export default function LoginPage() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onGoogle() {
    setError(null);
    setLoading(true);
    try {
      await signIn.social({
        provider: "google",
        callbackURL: "/",
        errorCallbackURL: "/login?error=denied",
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
