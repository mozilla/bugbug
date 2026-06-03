import type { Metadata } from "next";

import "./globals.css";
import { UserMenu } from "@/components/UserMenu";

export const metadata: Metadata = {
  title: "Hackbot Launchpad",
  description: "Launch and observe hackbot agents",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <header className="topbar">
          <div className="inner">
            <h1>🚀 Hackbot Launchpad</h1>
            <span className="tag">demo</span>
            <div style={{ marginLeft: "auto" }}>
              <UserMenu />
            </div>
          </div>
        </header>
        <main className="container">{children}</main>
      </body>
    </html>
  );
}
