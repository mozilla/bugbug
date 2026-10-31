# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Calibration of the perf regression predictor's risk scores.

The predictor's raw ``risk_score`` orders commits by risk but is not a
probability: the model was trained on far more positives than autoland
produces, so a raw 0.6 is not a 60 percent chance of a regression. The
risk-aware batching strategies in the taskgraph add commit scores up as
hazards, ``-log(1 - score)``, which is the probability that at least one
pending commit regressed only when each score is a probability, and a
threshold tuned on raw scores drifts as the score distribution moves.

``fit_calibration`` learns a monotone mapping from raw scores to observed
culprit rates on labeled history, by isotonic regression over equal-count
bins of commits so that no step rests on a handful of them, stored as
breakpoints that ``Calibration.apply`` interpolates without scikit-learn. The
pipeline's ``calibrate`` command builds one record per model version into
``perf_risk_calibration.json``; the HTTP service applies the published mapping
before answering and the simulator fits its own on its optimize window.
Calibration never reorders commits: it changes what a threshold means, not
which commits rank highest.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from bugbug.perf.label_policy import POLICY_VERSION, POSITIVE_OUTCOMES
from bugbug.perf.push_status import iter_clean_commits
from bugbug.utils import parse_timestamp, utcnow

logger = logging.getLogger(__name__)

METHOD = "isotonic"
# Commits per bin before the isotonic fit: the highest step of the mapping,
# where the batching thresholds operate, then rests on at least this many
# commits instead of on the one or two top-scoring ones.
BIN_SIZE = 50
# Below this many culprits the tail of the mapping is noise whatever the bins.
DEFAULT_MIN_POSITIVES = 50
DEFAULT_HOLDOUT_DAYS = 60
TOP_SHARES = (0.01, 0.02, 0.05)
ECE_BINS = 10


@dataclass(frozen=True)
class Calibration:
    """A monotone mapping from raw risk scores to probabilities.

    ``x`` are increasing raw scores and ``y`` the non-decreasing probability
    at each; a score between two breakpoints interpolates linearly and one
    outside them takes the nearest end.
    """

    model_version: str
    x: tuple[float, ...]
    y: tuple[float, ...]
    fitted_at: str | None = None

    def apply(self, score: float) -> float:
        return float(np.interp(score, self.x, self.y))

    def to_record(self) -> dict[str, Any]:
        return {
            "model_version": self.model_version,
            "method": METHOD,
            "x": list(self.x),
            "y": list(self.y),
            "fitted_at": self.fitted_at,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "Calibration":
        return cls(
            record["model_version"],
            tuple(record["x"]),
            tuple(record["y"]),
            record.get("fitted_at"),
        )


@dataclass(frozen=True)
class LabeledScore:
    node: str
    time: datetime
    score: float
    label: int


def scores_by_version(
    records: Iterable[Mapping[str, Any]],
) -> dict[str, dict[str, float]]:
    """Raw scores of ``perf_commit_risk.json`` records, by model version then node."""
    scores: dict[str, dict[str, float]] = {}
    for record in records:
        if record["risk_score"] is not None:
            scores.setdefault(record["model_version"], {})[record["node"]] = record[
                "risk_score"
            ]
    return scores


def label_scored_commits(
    regressions: Iterable[dict[str, Any]],
    statuses: Iterable[dict[str, Any]],
    scores: Mapping[str, float],
) -> list[LabeledScore]:
    """Scored commits with a known label, in push order.

    Positives are the pinned culprit commits of regressions with a positive
    outcome. Negatives are the commits of settled clean pushes, the same
    source as the training sets. Everything else has no label and is left
    out: the other commits of a culprit push, the commits of unpinned
    regressions, pending and rejected pushes, and commits without a score.
    """
    rows: dict[str, LabeledScore] = {}
    for regression in regressions:
        if regression["outcome"] not in POSITIVE_OUTCOMES:
            continue
        time = parse_timestamp(regression["push_time"])
        if time is None:
            continue
        for node in regression["culprit_commits"]:
            score = scores.get(node)
            if score is not None:
                rows[node] = LabeledScore(node, time, score, 1)
    for status, commit in iter_clean_commits(statuses):
        node = commit["node"]
        score = scores.get(node)
        time = parse_timestamp(status["push_time"])
        if score is not None and time is not None and node not in rows:
            rows[node] = LabeledScore(node, time, score, 0)
    return sorted(rows.values(), key=lambda row: (row.time, row.node))


def fit_calibration(
    scores: Sequence[float],
    labels: Sequence[int],
    model_version: str,
    fitted_at: str | None = None,
    bin_size: int = BIN_SIZE,
) -> Calibration:
    """Isotonic regression of the culprit rate on the score, kept as breakpoints.

    The commits are sorted by score and cut into bins of at least ``bin_size``;
    the regression fits each bin's culprit rate at its mean score, weighted by
    its size, so a step of the mapping never rests on fewer commits than a
    bin. scikit-learn interpolates linearly between the breakpoints it keeps
    after pooling adjacent violators, so ``Calibration.apply`` reproduces its
    predictions; scores beyond the outermost bin means take the end values.
    """
    from sklearn.isotonic import IsotonicRegression

    s = np.asarray(scores, dtype=float)
    y = np.asarray(labels, dtype=float)
    bins = np.array_split(np.argsort(s, kind="stable"), max(1, len(s) // bin_size))
    isotonic = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
    isotonic.fit(
        [s[b].mean() for b in bins],
        [y[b].mean() for b in bins],
        sample_weight=[len(b) for b in bins],
    )
    return Calibration(
        model_version,
        tuple(float(value) for value in isotonic.X_thresholds_),
        tuple(float(value) for value in isotonic.y_thresholds_),
        fitted_at,
    )


def reliability_metrics(
    probabilities: Sequence[float], labels: Sequence[int]
) -> dict[str, float]:
    """Log loss, Brier score and expected calibration error over equal-count bins.

    Equal-count bins, rather than equal-width ones, because nearly every
    commit scores close to zero and equal-width bins would leave the rest
    nearly empty.
    """
    from sklearn.metrics import brier_score_loss, log_loss

    p = np.clip(np.asarray(probabilities, dtype=float), 1e-6, 1 - 1e-6)
    y = np.asarray(labels, dtype=float)
    order = np.argsort(p, kind="stable")
    ece = sum(
        len(chunk) / len(order) * abs(p[chunk].mean() - y[chunk].mean())
        for chunk in np.array_split(order, min(ECE_BINS, len(order)))
    )
    return {
        "log_loss": float(log_loss(y, p, labels=[0, 1])),
        "brier": float(brier_score_loss(y, p)),
        "ece": float(ece),
    }


def ranking_metrics(
    scores: Sequence[float], labels: Sequence[int]
) -> dict[str, float | None]:
    """Precision and recall of the top 1, 2 and 5 percent of commits by score."""
    s = np.asarray(scores, dtype=float)
    y = np.asarray(labels, dtype=float)
    order = np.argsort(-s, kind="stable")
    positives = y.sum()
    metrics: dict[str, float | None] = {}
    for share in TOP_SHARES:
        top = max(1, round(len(order) * share))
        hits = y[order[:top]].sum()
        percent = round(share * 100)
        metrics[f"precision_at_{percent}_percent"] = float(hits / top)
        metrics[f"recall_at_{percent}_percent"] = (
            float(hits / positives) if positives else None
        )
    return metrics


def build_calibration_record(
    rows: Sequence[LabeledScore],
    model_version: str,
    holdout_days: int = DEFAULT_HOLDOUT_DAYS,
    min_positives: int = DEFAULT_MIN_POSITIVES,
    fitted_at: datetime | None = None,
) -> dict[str, Any] | None:
    """Fit a model version's calibration and record how well it holds up.

    The newest ``holdout_days`` of rows are scored by a mapping fitted on the
    older rows, raw and calibrated, as the record's ``holdout`` metrics; the
    published mapping is then fitted on every row. Returns None below
    ``min_positives`` culprits.
    """
    positives = sum(row.label for row in rows)
    if positives < min_positives:
        logger.warning(
            "%s: %d culprits among %d labeled scored commits, below the %d needed "
            "to calibrate",
            model_version,
            positives,
            len(rows),
            min_positives,
        )
        return None

    fitted_at = fitted_at or utcnow()
    calibration = fit_calibration(
        [row.score for row in rows],
        [row.label for row in rows],
        model_version,
        fitted_at.isoformat(),
    )
    record: dict[str, Any] = {
        **calibration.to_record(),
        "fit_window": {
            "since": rows[0].time.isoformat(),
            "until": rows[-1].time.isoformat(),
        },
        "commits": len(rows),
        "positives": positives,
        "base_rate": positives / len(rows),
        "bin_size": BIN_SIZE,
        "holdout": None,
        "policy_version": POLICY_VERSION,
    }

    cutoff = rows[-1].time - timedelta(days=holdout_days)
    older = [row for row in rows if row.time < cutoff]
    newer = [row for row in rows if row.time >= cutoff]
    labels = [row.label for row in newer]
    if any(row.label for row in older) and 0 < sum(labels) < len(labels):
        trial = fit_calibration(
            [row.score for row in older], [row.label for row in older], model_version
        )
        raw = [row.score for row in newer]
        record["holdout"] = {
            "since": newer[0].time.isoformat(),
            "until": newer[-1].time.isoformat(),
            "commits": len(newer),
            "positives": sum(labels),
            "raw": reliability_metrics(raw, labels),
            "calibrated": reliability_metrics([trial.apply(s) for s in raw], labels),
            "ranking": ranking_metrics(raw, labels),
        }
    return record


def load_calibrations(
    records: Iterable[Mapping[str, Any]],
) -> dict[str, Calibration]:
    """The calibration of each model version in ``perf_risk_calibration.json`` records."""
    return {
        record["model_version"]: Calibration.from_record(record) for record in records
    }
