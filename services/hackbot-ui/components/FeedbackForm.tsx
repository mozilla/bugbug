"use client";

import { useState } from "react";

import { Markdown } from "@/components/Markdown";
import {
  FEEDBACK_DIMENSIONS,
  type FeedbackDimension,
  type FeedbackRating,
} from "@/lib/types";

const MAX_COMMENT = 5000;

export function FeedbackForm({
  token,
  nonce,
  bugId,
  comment,
  initialRating,
}: {
  token: string;
  nonce: string;
  bugId: number;
  comment: string;
  initialRating: FeedbackRating | null;
}) {
  const [rating, setRating] = useState<FeedbackRating | null>(initialRating);
  const [dimensions, setDimensions] = useState<FeedbackDimension[]>([]);
  const [note, setNote] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  function toggle(dimension: FeedbackDimension) {
    setDimensions((current) =>
      current.includes(dimension)
        ? current.filter((d) => d !== dimension)
        : [...current, dimension]
    );
  }

  async function submit() {
    if (!rating) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await fetch(`/api/rate/${encodeURIComponent(token)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          rating,
          nonce,
          dimensions,
          comment: note.trim() || null,
        }),
      });
      // Parse defensively: an unexpected status can carry an HTML error page,
      // and letting res.json() throw on it would replace a readable status
      // with a JSON syntax error.
      const body = await res.json().catch(() => null);
      if (!res.ok) {
        throw new Error(body?.error ?? `${res.status} ${res.statusText}`);
      }
      setDone(body?.message ?? "Feedback recorded. Thank you.");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  if (done) {
    return <p className="feedback-done">{done}</p>;
  }

  return (
    <>
      <h2>Was this analysis useful?</h2>
      <p className="muted">
        Hackbot posted the comment below on{" "}
        <a
          href={`https://bugzilla.mozilla.org/show_bug.cgi?id=${bugId}`}
          target="_blank"
          rel="noreferrer"
        >
          bug {bugId}
        </a>
        . Your rating helps us improve it. No account needed.
      </p>

      <div className="action-preview">
        <span className="muted">Comment being rated</span>
        <Markdown text={comment} />
      </div>

      <div className="feedback-thumbs">
        <button
          type="button"
          className={rating === "up" ? "selected" : ""}
          aria-pressed={rating === "up"}
          onClick={() => setRating("up")}
        >
          👍 Useful
        </button>
        <button
          type="button"
          className={rating === "down" ? "selected" : ""}
          aria-pressed={rating === "down"}
          onClick={() => setRating("down")}
        >
          👎 Not useful
        </button>
      </div>

      {rating === "down" && (
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
      )}

      <label className="feedback-note">
        <span className="muted">Anything else? (optional)</span>
        <textarea
          value={note}
          maxLength={MAX_COMMENT}
          rows={4}
          onChange={(e) => setNote(e.target.value)}
        />
      </label>

      {error && <div className="error-banner">{error}</div>}

      <button type="button" onClick={submit} disabled={!rating || submitting}>
        {submitting ? "Sending…" : "Submit feedback"}
      </button>
    </>
  );
}
