# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Cost and detection metrics of a schedule over a window of the timeline."""

import collections
import statistics
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Mapping

from bugbug.perf.simulation.capacity import CiResult
from bugbug.perf.simulation.detection import Detection, Schedule
from bugbug.perf.simulation.stats import bootstrap_mean, percentile
from bugbug.perf.simulation.strategies import ALL
from bugbug.perf.simulation.timeline import Timeline

Latency = Literal[
    "detect",
    "detect_pushes",
    "window_pushes",
    "detect_ci",
    "identify",
]
# Metric key of each latency and the unit it is counted in. The last two
# need the CI capacity model: detection at the completion of the catching run
# and identification once the sheriff's backfills pin the culprit.
LATENCY_KEYS: dict[str, tuple[str, str]] = {
    "detect": ("detect_hours", "hours"),
    "detect_pushes": ("detect_pushes", "pushes"),
    "window_pushes": ("window_pushes", "pushes"),
    "detect_ci": ("detect_ci_hours", "hours"),
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
        "p90": percentile(ordered, 0.9),
        "max": ordered[-1],
    }


def _latency(observed: dict[int, float]) -> dict[str, Any]:
    """Summary of a latency over the regressions it exists for, with their ids."""
    return {**_summary(list(observed.values())), "ids": sorted(observed)}


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
    culprit, over the regressions where that run exists in the timeline. With
    a CI replay ``ci``, ``detect_ci_hours`` runs to the completion of the
    catching run and ``identify_hours`` to the sheriff's backfills pinning
    the culprit; ``backfill_jobs`` counts those backfills and ``total_jobs``
    adds them to the schedule's own jobs. Each latency summary carries the
    ``ids`` of the regressions
    it is taken over, since a later event can be missing where an earlier
    one exists.
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
    detect_hours = {
        d.regression.summary_id: d.detect_hours(timeline) or 0.0 for d in detected
    }
    detect_pushes = {
        d.regression.summary_id: float(d.detect_pushes or 0) for d in detected
    }
    window_pushes = {
        d.regression.summary_id: float(d.window_pushes or 0) for d in detected
    }
    latencies = {
        "detect_hours": detect_hours,
        "detect_pushes": detect_pushes,
        "window_pushes": window_pushes,
    }
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
        "detected_ids": sorted(d.regression.summary_id for d in detected),
        "missed_ids": sorted(
            d.regression.summary_id for d in selected if d.detected_index is None
        ),
        **{key: _latency(values) for key, values in latencies.items()},
        "merged": sum(count - 1 for count in caught_at.values() if count > 1),
    }
    if ci is not None:

        def hours_until(times: Mapping[int, datetime | None]) -> dict[int, float]:
            return {
                d.regression.summary_id: (when - d.regression.time).total_seconds()
                / 3600
                for d in detected
                if (when := times.get(d.regression.summary_id)) is not None
            }

        detect_ci = hours_until(ci.detect_time)
        identify = hours_until(ci.identify_time)
        backfills = sum(
            ci.backfill_jobs.get(d.regression.summary_id, 0) for d in selected
        )
        result["detect_ci_hours"] = _latency(detect_ci)
        result["identify_hours"] = _latency(identify)
        result["identified"] = len(identify)
        result["backfill_jobs"] = backfills
        result["total_jobs"] = jobs + backfills
        latencies.update({"detect_ci_hours": detect_ci, "identify_hours": identify})
    if bootstrap_samples:
        for key, values in latencies.items():
            result[key]["mean_ci95"] = bootstrap_mean(
                list(values.values()), bootstrap_samples
            )
    return result


def missed_ids(
    result: dict[str, Any], baseline: dict[str, Any], latency: Latency = "detect"
) -> list[int]:
    """Regressions the baseline has the latency for and the schedule does not.

    Detection alone is not enough: a regression caught in push order may have
    no CI completion or identification, its label having no pool or the
    backfills being off, and a schedule that lacks such later events for more
    regressions than the baseline would be summarized over fewer, earlier
    ones and look faster for it.
    """
    key = latency_key(latency)
    if key not in result or key not in baseline:
        raise ValueError(f"{latency} needs the CI capacity model; pass --capacity")
    return sorted(set(baseline[key]["ids"]) - set(result[key]["ids"]))


def is_feasible(
    result: dict[str, Any], baseline: dict[str, Any], latency: Latency = "detect"
) -> bool:
    """Whether the schedule has the latency for every regression the baseline has it for."""
    return not missed_ids(result, baseline, latency)


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

    ``detect`` is hours from the culprit push; ``detect_pushes``
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
