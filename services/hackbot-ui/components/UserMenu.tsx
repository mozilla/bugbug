"use client";

import { useRouter } from "next/navigation";

import { signOut, useSession } from "@/lib/auth-client";

export function UserMenu() {
  const router = useRouter();
  const { data: session, isPending } = useSession();

  if (isPending || !session?.user) {
    return null;
  }

  async function onSignOut() {
    await signOut();
    router.push("/login");
    router.refresh();
  }

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
      <span className="muted" style={{ fontSize: 13 }}>
        {session.user.email}
      </span>
      <button className="secondary" onClick={onSignOut}>
        Sign out
      </button>
    </div>
  );
}
