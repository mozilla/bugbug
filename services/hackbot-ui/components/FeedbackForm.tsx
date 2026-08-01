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
  comment,
  initialRating,
}: {
  token: string;
  nonce: string;
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
      const res = await fetch(`/api/feedback/${encodeURIComponent(token)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          rating,
          nonce,
          dimensions,
          comment: note.trim() || null,
        }),
      });
      const body = await res.json();
      if (!res.ok) {
        throw new Error(body?.error ?? `${res.status} ${res.statusText}`);
      }
      setDone(body.message ?? "Feedback recorded. Thank you.");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  if (done) {
    return <p>{done}</p>;
  }

  return (
    <>
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

      {error && <p className="muted">{error}</p>}

      <button type="button" onClick={submit} disabled={!rating || submitting}>
        {submitting ? "Sending…" : "Submit feedback"}
      </button>
    </>
  );
}
