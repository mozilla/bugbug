# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Cost and detection metrics of a schedule over a window of the timeline."""

import collections
import random
import statistics
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from bugbug.perf.simulation.capacity import CiResult
from bugbug.perf.simulation.detection import Detection, Schedule
from bugbug.perf.simulation.strategies import ALL
from bugbug.perf.simulation.timeline import Timeline

Latency = Literal[
    "surface",
    "detect",
    "detect_pushes",
    "window_pushes",
    "detect_ci",
    "surface_ci",
    "identify",
]
# Metric key of each latency and the unit it is counted in. The last three
# need the CI capacity model: detection at the completion of the catching run,
# the alert under the replay's alerting mode, and identification once the
# sheriff's backfills pin the culprit.
LATENCY_KEYS: dict[str, tuple[str, str]] = {
    "detect": ("detect_hours", "hours"),
    "surface": ("surface_hours", "hours"),
    "detect_pushes": ("detect_pushes", "pushes"),
    "window_pushes": ("window_pushes", "pushes"),
    "detect_ci": ("detect_ci_hours", "hours"),
    "surface_ci": ("surface_ci_hours", "hours"),
    "identify": ("identify_hours", "hours"),
}
Cost = Literal["batches", "jobs", "total_jobs"]
Statistic = Literal["mean", "median", "p90", "max"]


@dataclass(frozen=True)
class Window:
    """Pushes with ``start <= time < end``; cost is counted and culprits are selected in it."""

    start: datetime
    end: datetime

    def contains(self, time: datetime) -> bool:
        return self.start <= time < self.end


def _summary(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"mean": None, "median": None, "p90": None, "max": None}
    ordered = sorted(values)
    return {
        "mean": statistics.fmean(ordered),
        "median": statistics.median(ordered),
        "p90": ordered[min(len(ordered) - 1, int(0.9 * len(ordered)))],
        "max": ordered[-1],
    }


def bootstrap_mean(
    values: list[float], samples: int = 1000, seed: int = 0
) -> tuple[float, float] | None:
    """95 percent percentile bootstrap interval of the mean, None below two values."""
    if len(values) < 2:
        return None
    rng = random.Random(seed)
    means = sorted(
        statistics.fmean(rng.choices(values, k=len(values))) for _ in range(samples)
    )
    return means[int(0.025 * samples)], means[min(samples - 1, int(0.975 * samples))]


def evaluate(
    timeline: Timeline,
    schedule: Schedule,
    detections: list[Detection],
    window: Window,
    bootstrap_samples: int = 0,
    ci: CiResult | None = None,
) -> dict[str, Any]:
    """Measure a schedule on the pushes and regressions whose time falls in ``window``.

    Cost counts the batches and jobs the schedule ran in the window. Detection
    covers the regressions whose culprit landed in the window, wherever they
    were caught: ``detect_hours`` is the time to the first run after the
    culprit and ``surface_hours`` the time until Perfherder would alert, over
    the regressions where that run exists in the timeline. With a CI replay
    ``ci``, ``detect_ci_hours`` runs to the completion of the catching run,
    ``surface_ci_hours`` to the alert under the replay's alerting mode and
    ``identify_hours`` to the sheriff's backfills pinning the culprit;
    ``backfill_jobs`` and ``confirm_jobs`` count those backfills and the
    confirming retriggers, and ``total_jobs`` adds both to the schedule's
    own jobs.
    """
    indices = [push.index for push in timeline.pushes if window.contains(push.time)]
    batch_indices = [i for i in indices if schedule.decisions[i] == ALL]
    jobs = sum(schedule.jobs_at(i, timeline) for i in indices)
    gaps_pushes = [b - a for a, b in zip(batch_indices, batch_indices[1:])]
    gaps_hours = [
        timeline.hours(a, b) for a, b in zip(batch_indices, batch_indices[1:])
    ]

    selected = [d for d in detections if window.contains(d.regression.time)]
    detected = [d for d in selected if d.detected_index is not None]
    surfaced = [d for d in detected if d.surfaced_index is not None]
    detect_hours = [d.detect_hours(timeline) or 0.0 for d in detected]
    surface_hours = [d.surface_hours(timeline) or 0.0 for d in surfaced]
    detect_pushes = [float(d.detect_pushes or 0) for d in detected]
    window_pushes = [float(d.window_pushes or 0) for d in detected]
    caught_at: collections.Counter = collections.Counter(
        (d.detected_index, d.label) for d in detected
    )

    result: dict[str, Any] = {
        "window": {
            "start": window.start.isoformat(),
            "end": window.end.isoformat(),
            "pushes": len(indices),
        },
        "batches": len(batch_indices),
        "jobs": jobs,
        "pushes_per_batch": _summary([float(g) for g in gaps_pushes]),
        "hours_between_batches": _summary(gaps_hours),
        "regressions": len(selected),
        "detected": len(detected),
        "surfaced": len(surfaced),
        "detected_ids": sorted(d.regression.summary_id for d in detected),
        "missed_ids": sorted(
            d.regression.summary_id for d in selected if d.detected_index is None
        ),
        "detect_hours": _summary(detect_hours),
        "surface_hours": _summary(surface_hours),
        "detect_pushes": _summary(detect_pushes),
        "window_pushes": _summary(window_pushes),
        "merged": sum(count - 1 for count in caught_at.values() if count > 1),
    }
    if bootstrap_samples:
        result["detect_hours"]["mean_ci95"] = bootstrap_mean(
            detect_hours, bootstrap_samples
        )
        result["surface_hours"]["mean_ci95"] = bootstrap_mean(
            surface_hours, bootstrap_samples
        )
        result["detect_pushes"]["mean_ci95"] = bootstrap_mean(
            detect_pushes, bootstrap_samples
        )
        result["window_pushes"]["mean_ci95"] = bootstrap_mean(
            window_pushes, bootstrap_samples
        )
    if ci is not None:
        detect_ci = [
            (
                ci.detect_time[d.regression.summary_id] - d.regression.time
            ).total_seconds()
            / 3600
            for d in detected
            if d.regression.summary_id in ci.detect_time
        ]
        surface_ci = [
            (when - d.regression.time).total_seconds() / 3600
            for d in detected
            if (when := ci.surface_time.get(d.regression.summary_id)) is not None
        ]
        identify = [
            (when - d.regression.time).total_seconds() / 3600
            for d in detected
            if (when := ci.identify_time.get(d.regression.summary_id)) is not None
        ]
        backfills = sum(
            ci.backfill_jobs.get(d.regression.summary_id, 0) for d in selected
        )
        confirms = sum(
            ci.confirm_jobs.get(d.regression.summary_id, 0) for d in selected
        )
        result["detect_ci_hours"] = _summary(detect_ci)
        result["surface_ci_hours"] = _summary(surface_ci)
        result["surfaced_ci"] = len(surface_ci)
        result["identify_hours"] = _summary(identify)
        result["identified"] = len(identify)
        result["backfill_jobs"] = backfills
        result["confirm_jobs"] = confirms
        result["total_jobs"] = jobs + backfills + confirms
        if bootstrap_samples:
            for key, values in (
                ("detect_ci_hours", detect_ci),
                ("surface_ci_hours", surface_ci),
                ("identify_hours", identify),
            ):
                result[key]["mean_ci95"] = bootstrap_mean(values, bootstrap_samples)
    return result


def is_feasible(result: dict[str, Any], baseline: dict[str, Any]) -> bool:
    """Whether the schedule caught every regression the baseline caught in the window."""
    return set(baseline["detected_ids"]) <= set(result["detected_ids"])


def latency_key(latency: str) -> str:
    """The metrics key holding the latency summary."""
    return LATENCY_KEYS[latency][0]


def latency_label(latency: str) -> str:
    """How to name the latency in a report, such as ``detect hours``."""
    key, unit = LATENCY_KEYS[latency]
    return f"{key.split('_')[0]} {unit}"


def objectives(
    result: dict[str, Any],
    cost: Cost = "jobs",
    latency: Latency = "detect",
    statistic: Statistic = "mean",
) -> tuple[float, float]:
    """The two values optuna minimizes: cost and a summary of the latency.

    ``detect`` and ``surface`` are hours from the culprit push; ``detect_pushes``
    counts the pushes from the culprit to the detecting run and ``window_pushes``
    the pushes a sheriff bisects, from the previous run to the detecting one.
    """
    key = latency_key(latency)
    if key not in result or cost not in result:
        raise ValueError(
            f"{latency} and total_jobs need the CI capacity model; pass --capacity"
        )
    value = result[key][statistic]
    return float(result[cost]), float("inf") if value is None else float(value)
