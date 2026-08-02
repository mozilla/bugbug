"use client";

import { useState } from "react";

import {
  FEEDBACK_DIMENSIONS,
  type FeedbackDimension,
  type FeedbackRating,
} from "@/lib/types";

// Rating for a comment the agent proposes, available before it is posted — the
// case worth capturing is a reviewer judging an analysis bad and declining to
// apply it. Attribution comes from the session, server-side.
export function ActionRating({ runId }: { runId: string }) {
  const [rating, setRating] = useState<FeedbackRating | null>(null);
  const [dimensions, setDimensions] = useState<FeedbackDimension[]>([]);
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function toggle(dimension: FeedbackDimension) {
    setDimensions((current) =>
      current.includes(dimension)
        ? current.filter((d) => d !== dimension)
        : [...current, dimension]
    );
  }

  async function submit(value: FeedbackRating) {
    setSaving(true);
    setError(null);
    try {
      const res = await fetch(`/api/runs/${runId}/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          rating: value,
          dimensions: value === "down" ? dimensions : [],
          comment: note.trim() || null,
        }),
      });
      const body = await res.json().catch(() => null);
      if (!res.ok) {
        throw new Error(body?.error ?? `${res.status} ${res.statusText}`);
      }
      setDone(true);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  }

  if (done) {
    return (
      <p className="muted action-rating">
        You rated this {rating === "up" ? "👍" : "👎"}. Thanks.
      </p>
    );
  }

  return (
    <div className="action-rating">
      <span className="muted">Rate this analysis</span>
      <div className="feedback-thumbs">
        <button
          type="button"
          className={rating === "up" ? "selected" : ""}
          aria-pressed={rating === "up"}
          disabled={saving}
          // A thumbs-up needs no explanation, so it saves on click. Thumbs-down
          // opens the form instead: the reason is the point.
          onClick={() => {
            setRating("up");
            submit("up");
          }}
        >
          👍 Useful
        </button>
        <button
          type="button"
          className={rating === "down" ? "selected" : ""}
          aria-pressed={rating === "down"}
          disabled={saving}
          onClick={() => setRating("down")}
        >
          👎 Not useful
        </button>
      </div>

      {rating === "down" && (
        <>
          <fieldset className="feedback-dimensions">
            <legend className="muted">What was wrong? (optional)</legend>
            {FEEDBACK_DIMENSIONS.map((dimension) => (
              <label key={dimension.value}>
                <input
                  type="checkbox"
                  checked={dimensions.includes(dimension.value)}
                  onChange={() => toggle(dimension.value)}
                />
                {dimension.label}
              </label>
            ))}
          </fieldset>
          <label className="feedback-note">
            <span className="muted">Anything else? (optional)</span>
            <textarea
              value={note}
              maxLength={5000}
              rows={3}
              onChange={(e) => setNote(e.target.value)}
            />
          </label>
          <button
            type="button"
            disabled={saving}
            onClick={() => submit("down")}
          >
            {saving ? "Saving…" : "Save rating"}
          </button>
        </>
      )}

      {error && <div className="error-banner">{error}</div>}
    </div>
  );
}
