"use client";

import { useEffect, useState } from "react";

import {
  describingCommits,
  parsePatch,
  type ChangesMeta,
  type PatchFile,
} from "@/lib/patch";

// The artifacts every agent that edits source publishes (see
// HackbotContext.publish_changes): the diff itself and what the runtime recorded
// about the commits behind it.
export const PATCH_ARTIFACT = "changes/changes.patch";
export const CHANGES_META_ARTIFACT = "changes/changes.json";

export function PatchPanel({
  files,
  meta = null,
  truncated = false,
}: {
  files: PatchFile[];
  meta?: ChangesMeta | null;
  truncated?: boolean;
}) {
  const added = files.reduce((n, f) => n + f.added, 0);
  const removed = files.reduce((n, f) => n + f.removed, 0);
  const commits = describingCommits(meta);

  return (
    <div className="panel">
      <h2>
        Patch ({files.length} {files.length === 1 ? "file" : "files"})
      </h2>
      {files.length === 0 ? (
        <p className="muted">The patch contains no file changes.</p>
      ) : (
        <>
          {commits.map((commit) => (
            <div key={commit.sha} className="patch-commit">
              <strong>{commit.subject}</strong>
              {commit.body.trim() && <p>{commit.body.trim()}</p>}
            </div>
          ))}
          {meta && commits.length === 0 && (
            <p className="muted patch-commit-missing">
              The agent left its changes uncommitted, so the patch carries no
              description.
            </p>
          )}
          <p className="muted patch-total">
            <span className="diff-add">+{added}</span>{" "}
            <span className="diff-del">−{removed}</span>
            {meta && <> · applies to {meta.base_commit.slice(0, 12)}</>}
          </p>
          {truncated && (
            <p className="muted">
              Only the first part of the patch is shown; download the artifact
              below for the rest.
            </p>
          )}
          {files.map((file) => (
            <div
              key={`${file.oldPath ?? ""}${file.path}`}
              className="patch-file"
            >
              <div className="patch-file-head">
                <code>
                  {file.oldPath ? `${file.oldPath} → ${file.path}` : file.path}
                </code>
                <span className="patch-file-meta">
                  {file.status !== "modified" && (
                    <span className="muted">{file.status}</span>
                  )}
                  <span className="diff-add">+{file.added}</span>
                  <span className="diff-del">−{file.removed}</span>
                </span>
              </div>
              {file.binary ? (
                <p className="muted patch-binary">Binary file not shown.</p>
              ) : (
                <pre className="diff">
                  {file.lines.map((line, idx) => (
                    <span key={idx} className={`diff-line ${line.kind}`}>
                      {line.text || " "}
                      {"\n"}
                    </span>
                  ))}
                </pre>
              )}
            </div>
          ))}
        </>
      )}
    </div>
  );
}

// Enough of a preview for a developer to decide whether to submit the patch for
// review; the review itself happens on the Phabricator revision, so this is
// deliberately read-only line highlighting with no diff options or commenting.
export function PatchView({ runId }: { runId: string }) {
  const [files, setFiles] = useState<PatchFile[] | null>(null);
  const [meta, setMeta] = useState<ChangesMeta | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function raw(artifact: string): Promise<{
      text: string;
      truncated: boolean;
    }> {
      const encoded = artifact.split("/").map(encodeURIComponent).join("/");
      const res = await fetch(
        `/api/runs/${encodeURIComponent(runId)}/artifacts/${encoded}?raw=1`
      );
      const body = await res.json();
      if (!res.ok)
        throw new Error(body?.error ?? `Request failed (${res.status})`);
      return body;
    }

    async function load() {
      try {
        const patch = await raw(PATCH_ARTIFACT);
        if (cancelled) return;
        setFiles(parsePatch(patch.text));
        setTruncated(Boolean(patch.truncated));
      } catch (err) {
        if (!cancelled) setError((err as Error).message);
        return;
      }
      // The description is a bonus on top of the diff: an older run without the
      // metadata artifact still renders, just without a commit message.
      try {
        const json = await raw(CHANGES_META_ARTIFACT);
        if (!cancelled) setMeta(JSON.parse(json.text) as ChangesMeta);
      } catch {
        // no metadata for this run
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
  if (!files) {
    return (
      <div className="panel">
        <h2>Patch</h2>
        <p className="muted">Loading patch…</p>
      </div>
    );
  }
  return <PatchPanel files={files} meta={meta} truncated={truncated} />;
}
