"use client";

import { html, parse } from "diff2html";
import { ColorSchemeType } from "diff2html/lib/types";
import { useEffect, useState } from "react";

// The artifact every agent that edits source publishes (see
// HackbotContext.publish_changes).
export const PATCH_ARTIFACT = "changes/changes.patch";

// Enough for any patch worth reading inline; past it, the artifact is a
// download rather than something to render in a panel.
const MAX_RENDERED_CHARS = 1024 * 1024;

export function PatchPanel({
  diff,
  truncated = false,
}: {
  diff: string;
  truncated?: boolean;
}) {
  const files = parse(diff);

  return (
    <div className="panel">
      <h2>
        Patch ({files.length} {files.length === 1 ? "file" : "files"})
      </h2>
      {files.length === 0 ? (
        <p className="muted">The patch contains no file changes.</p>
      ) : (
        <>
          {truncated && (
            <p className="muted">
              Only the first part of the patch is shown; download the artifact
              below for the rest.
            </p>
          )}
          {/* diff2html only emits markup for the diff it was given and escapes
              its content, so this does not render arbitrary agent HTML. */}
          <div
            className="patch-diff"
            dangerouslySetInnerHTML={{
              __html: html(files, {
                drawFileList: files.length > 1,
                matching: "lines",
                colorScheme: ColorSchemeType.DARK,
              }),
            }}
          />
        </>
      )}
    </div>
  );
}

// Enough of a preview for a developer to decide whether to submit the patch for
// review; the review itself happens on the Phabricator revision, so this is
// deliberately read-only rendering with no diff options or commenting.
export function PatchView({ runId }: { runId: string }) {
  const [diff, setDiff] = useState<string | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const encoded = PATCH_ARTIFACT.split("/")
          .map(encodeURIComponent)
          .join("/");
        // The artifact route 302s to a signed GCS URL; the bucket's CORS config
        // is what lets this cross-origin read follow the redirect.
        const res = await fetch(
          `/api/runs/${encodeURIComponent(runId)}/artifacts/${encoded}`
        );
        if (!res.ok) throw new Error(`Could not download it (${res.status})`);
        const text = await res.text();
        if (cancelled) return;
        setDiff(text.slice(0, MAX_RENDERED_CHARS));
        setTruncated(text.length > MAX_RENDERED_CHARS);
      } catch (err) {
        if (!cancelled) setError((err as Error).message);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [runId]);

  if (error) {
    return (
      <div className="panel">
        <h2>Patch</h2>
        <div className="error-banner">Could not load patch: {error}</div>
      </div>
    );
  }
  if (diff === null) {
    return (
      <div className="panel">
        <h2>Patch</h2>
        <p className="muted">Loading patch…</p>
      </div>
    );
  }
  return <PatchPanel diff={diff} truncated={truncated} />;
}
