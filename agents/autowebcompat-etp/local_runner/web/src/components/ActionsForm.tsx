import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useSnapshot, type Snapshot } from "valtio";

import {
  applyActions,
  exceptionTagsOptional,
  needinfoReporter,
  setAddExceptions,
  toggleAttachment,
} from "../actions";
import { RESOLUTIONS, type Applied, type Resolution } from "../api";
import { store } from "../store";

function describe(applied: Snapshot<Applied>): string {
  return applied.actions
    .map((a) => {
      // Records from before screenshots were combined name a single stage.
      const stages = a.stages ?? (a.stage ? [a.stage] : []);
      return a.action + (stages.length ? ` (${stages.join(", ")})` : "");
    })
    .join(", ");
}

/** The reviewed Bugzilla changes: edit the draft, then post as yourself. */
export function ActionsForm() {
  const { config, run } = useSnapshot(store);
  // Edited in place; the sync snapshot keeps the caret steady while typing.
  const actions = store.run!.actions!;
  const snap = useSnapshot(actions, { sync: true });
  if (!run?.review || !config) return null;
  const { screenshots, screenshot_labels, applied } = run.review;
  const stages = Object.keys(screenshots);
  const reporter = needinfoReporter(snap, run.review);

  return (
    <>
      <h2>Bugzilla actions</h2>
      {applied && (
        <div className={`note ${applied.error ? "err" : "ok"}`}>
          Applied {applied.applied_at} to{" "}
          <a href={applied.url} target="_blank">
            bug {applied.bug_id}
          </a>
          : {describe(applied)}
          {applied.error && (
            <>
              <br />
              Error: {applied.error}
            </>
          )}
        </div>
      )}
      <div className="card">
        <div className="meta">
          {snap.whoamiError ? (
            <span className="err-text">
              Bugzilla auth failed: {snap.whoamiError}
            </span>
          ) : snap.whoami ? (
            <>
              Posting to {config.bugzilla} as{" "}
              <b>{snap.whoami.real_name || snap.whoami.name}</b> (
              {snap.whoami.name})
            </>
          ) : (
            "Checking Bugzilla account…"
          )}
        </div>

        <label>Bug ID</label>
        <input
          type="number"
          value={snap.bugId}
          onChange={(e) => (actions.bugId = e.target.value)}
        />

        <div className="tabs">
          <label>Comment (Markdown; leave empty to skip)</label>
          <span>
            <button
              type="button"
              className={`tab ${snap.preview ? "" : "on"}`}
              onClick={() => (actions.preview = false)}
            >
              Edit
            </button>
            <button
              type="button"
              className={`tab ${snap.preview ? "on" : ""}`}
              onClick={() => (actions.preview = true)}
            >
              Preview
            </button>
          </span>
        </div>
        {snap.preview ? (
          <div className="markdown">
            <Markdown remarkPlugins={[remarkGfm]}>{snap.comment}</Markdown>
          </div>
        ) : (
          <textarea
            rows={18}
            value={snap.comment}
            onChange={(e) => (actions.comment = e.target.value)}
          />
        )}

        <label>
          Field changes (JSON for PUT /bug, e.g.{" "}
          <code>{'{"whiteboard": "…", "keywords": {"add": ["…"]}}'}</code>)
        </label>
        <textarea
          rows={3}
          value={snap.changes}
          placeholder={
            snap.bugDraft ? undefined : "Drafting from the bug's fields…"
          }
          onChange={(e) => (actions.changes = e.target.value)}
        />
        {exceptionTagsOptional(run.review.result) && (
          <label className="check">
            <input
              type="checkbox"
              checked={snap.addExceptions}
              disabled={!snap.bugDraft}
              onChange={(e) => setAddExceptions(e.target.checked)}
            />
            Tag the bug to add the proposed exceptions (they only get around an
            anti-adblock wall)
          </label>
        )}

        <label>Attach screenshots</label>
        {stages.length ? (
          stages.map((stage) => (
            <label className="check" key={stage}>
              <input
                type="checkbox"
                checked={snap.attachments.includes(stage)}
                onChange={(e) => toggleAttachment(stage, e.target.checked)}
              />
              {screenshot_labels[stage] ?? stage}
            </label>
          ))
        ) : (
          <div className="meta">No screenshots.</div>
        )}

        <label className="check">
          <input
            type="checkbox"
            checked={snap.close}
            onChange={(e) => (actions.close = e.target.checked)}
          />
          Close the bug as RESOLVED{" "}
          <select
            value={snap.resolution}
            onChange={(e) =>
              (actions.resolution = e.target.value as Resolution)
            }
          >
            {RESOLUTIONS.map((r) => (
              <option key={r} value={r}>
                {r}
                {r === run.review!.draft.resolution ? " (suggested)" : ""}
              </option>
            ))}
          </select>
        </label>

        <label className="check">
          <input
            type="checkbox"
            checked={snap.needinfo && !!reporter}
            disabled={!reporter}
            onChange={(e) => (actions.needinfo = e.target.checked)}
          />
          Needinfo the reporter (
          {reporter ??
            (!snap.bugDraft
              ? "looking up…"
              : snap.bugId !== String(run.review.draft.bug_id)
                ? `only for bug ${run.review.draft.bug_id}`
                : "unknown")}
          ) to confirm the issue is fixed
        </label>

        {applied && (
          <label className="check">
            <input
              type="checkbox"
              checked={snap.force}
              onChange={(e) => (actions.force = e.target.checked)}
            />
            Apply again (already applied once)
          </label>
        )}

        <p>
          <button
            onClick={applyActions}
            disabled={snap.busy || !snap.bugDraft || (!!applied && !snap.force)}
          >
            Post to Bugzilla
          </button>
        </p>
        {snap.error && <div className="note err">{snap.error}</div>}
        {snap.done && (
          <div className="note ok">
            Done:{" "}
            <a href={snap.done.url} target="_blank">
              bug {snap.done.bug_id}
            </a>{" "}
            — {describe(snap.done)}
          </div>
        )}
      </div>
    </>
  );
}
