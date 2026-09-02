"use client";

import { use, useState } from "react";

import { signIn } from "@/lib/auth-client";

type LoginPageProps = {
  searchParams: Promise<{
    callbackURL?: string | string[];
    error?: string | string[];
  }>;
};

export default function LoginPage({ searchParams }: LoginPageProps) {
  const params = use(searchParams);
  const callbackURL =
    typeof params.callbackURL === "string" ? params.callbackURL : "/";
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onGoogle() {
    setError(null);
    setLoading(true);

    // Keep the target across a denied sign-in so a retry still lands on it.
    const errorParams = new URLSearchParams({ error: "denied", callbackURL });

    try {
      await signIn.social({
        provider: "google",
        callbackURL,
        errorCallbackURL: `/login?${errorParams.toString()}`,
      });
    } catch (err) {
      setError((err as Error).message);
      setLoading(false);
    }
  }

  const denied = params.error === "denied";

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
