# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""The CI worker pools perf tests run on, and a queue replay of a schedule over them.

A batch is a burst: the whole suite lands at once on pools that are mostly
fixed hardware, so results arrive as workers free up, not at push time.
``CapacityModel`` holds what that takes to replay: each pool's workers by
month, each label's pool and typical run time, and the hourly occupancy of
the pools by everything that is not autoland perf work, which the schedule
replaces. ``simulate_ci`` pushes a schedule's runs through one FIFO queue per
pool, eligible a build time after the push, then the sheriff's backfills
once a regression's alert is raised, and returns when each regression was
detected, alerted on and identified. A regression with several regressed
labels is caught by whichever label's run completes first, which need not be
the one scheduled first: labels queue on different pools.
"""

import collections
import hashlib
import heapq
import json
import logging
import math
import re
import statistics
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any

from bugbug.perf.simulation.detection import Detection, Schedule
from bugbug.perf.simulation.strategies import ALL
from bugbug.perf.simulation.timeline import FULL_SUITE_MIN_LABELS, Timeline
from bugbug.perf.task_labels import normalize_task_label

logger = logging.getLogger(__name__)

DEFAULT_BUILD_MINUTES = 99.0
# Pushes a sheriff backfills when no earlier run of the label is known.
MAX_BACKFILL_PUSHES = 20
PERF_LABEL_SQL = (
    '(tags.label LIKE "%-browsertime-%" OR tags.label LIKE "%-talos-%" '
    'OR tags.label LIKE "%-awsy%" OR tags.label LIKE "%-raptor-%" '
    'OR tags.label LIKE "perftest-%")'
)
TASKCLUSTER_ROOT = "https://firefox-ci-tc.services.mozilla.com/api"
_PLATFORM_PREFIX = re.compile(r"^(test-[^/]+/[a-z0-9]+)")


@dataclass
class PoolCapacity:
    """One worker pool: workers by month and what autoland perf runs saw on it."""

    name: str
    workers_by_month: dict[str, int]
    listed_workers: int | None = None
    observed_wait_minutes: dict[str, float] = field(default_factory=dict)
    observed_run_minutes: dict[str, float] = field(default_factory=dict)
    runs_per_day: float = 0.0
    # Share of the workers effectively serving, fitted by ``calibrate_scales``.
    scale: float = 1.0
    # Median minutes from a push to its tests on this pool becoming eligible:
    # the build and its signing. None falls back to the settings' build time.
    eligible_minutes: float | None = None
    # Share of pushes without a full perf suite that still carry the shippable
    # build this pool's tests need, because other tests on the push needed
    # it; None when the pool's build is unknown.
    build_share: float | None = None

    def workers_at(self, time: datetime) -> int:
        month = time.strftime("%Y-%m")
        if month in self.workers_by_month:
            return self.workers_by_month[month]
        months = sorted(self.workers_by_month)
        if not months:
            return self.listed_workers or 1
        earlier = [m for m in months if m < month]
        return self.workers_by_month[earlier[-1] if earlier else months[0]]


@dataclass(frozen=True)
class LabelRuntime:
    pool: str
    minutes: float


class CapacityModel:
    """Pools, labels and background load; see the module docstring."""

    def __init__(
        self,
        pools: dict[str, PoolCapacity],
        labels: dict[str, LabelRuntime],
        background: dict[str, dict[int, float]],
        meta: dict[str, Any] | None = None,
    ) -> None:
        self.pools = pools
        self.labels = labels
        self.background = background
        self.meta = meta or {}
        self._prefix_pool = self._prefix_pools(labels)
        self._pool_minutes = {
            pool: statistics.median(
                [r.minutes for r in labels.values() if r.pool == pool] or [15.0]
            )
            for pool in pools
        }
        self._all_minutes = statistics.median(
            [r.minutes for r in labels.values()] or [15.0]
        )

    @staticmethod
    def _prefix_pools(labels: dict[str, LabelRuntime]) -> dict[str, str]:
        counts: dict[str, collections.Counter] = collections.defaultdict(
            collections.Counter
        )
        for label, runtime in labels.items():
            match = _PLATFORM_PREFIX.match(label)
            if match:
                counts[match.group(1)][runtime.pool] += 1
        return {prefix: c.most_common(1)[0][0] for prefix, c in counts.items()}

    def pool_of(self, label: str) -> str | None:
        """The label's pool, else the pool its platform's other labels use."""
        runtime = self.labels.get(label)
        if runtime is not None:
            return runtime.pool
        match = _PLATFORM_PREFIX.match(label)
        return self._prefix_pool.get(match.group(1)) if match else None

    def minutes_of(self, label: str, pool: str | None) -> float:
        runtime = self.labels.get(label)
        if runtime is not None:
            return runtime.minutes
        if pool is not None and pool in self._pool_minutes:
            return self._pool_minutes[pool]
        return self._all_minutes

    def available(self, pool: str, time: datetime, scale: float = 1.0) -> int:
        """Workers free for autoland perf runs at ``time``: the pool minus its other work.

        ``scale`` shrinks the pool to what is effectively serving: workers seen
        in a month include machines that were down or quarantined part of it.
        """
        hour = int(time.timestamp() // 3600)
        busy = self.background.get(pool, {}).get(hour, 0.0)
        capacity = self.pools[pool]
        return max(1, round(scale * capacity.scale * capacity.workers_at(time) - busy))

    def to_dict(self) -> dict[str, Any]:
        return {
            "meta": self.meta,
            "pools": {
                name: {
                    "workers_by_month": pool.workers_by_month,
                    "listed_workers": pool.listed_workers,
                    "observed_wait_minutes": pool.observed_wait_minutes,
                    "observed_run_minutes": pool.observed_run_minutes,
                    "runs_per_day": pool.runs_per_day,
                    "scale": pool.scale,
                    "eligible_minutes": pool.eligible_minutes,
                    "build_share": pool.build_share,
                }
                for name, pool in self.pools.items()
            },
            "labels": {
                label: [runtime.pool, runtime.minutes]
                for label, runtime in self.labels.items()
            },
            "background": {
                pool: sorted(hours.items()) for pool, hours in self.background.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CapacityModel":
        pools = {
            name: PoolCapacity(
                name,
                dict(pool["workers_by_month"]),
                pool.get("listed_workers"),
                dict(pool.get("observed_wait_minutes", {})),
                dict(pool.get("observed_run_minutes", {})),
                float(pool.get("runs_per_day", 0.0)),
                float(pool.get("scale", 1.0)),
                pool.get("eligible_minutes"),
                pool.get("build_share"),
            )
            for name, pool in data["pools"].items()
        }
        labels = {
            label: LabelRuntime(pool, float(minutes))
            for label, (pool, minutes) in data["labels"].items()
        }
        background = {
            pool: {int(hour): float(busy) for hour, busy in hours}
            for pool, hours in data["background"].items()
        }
        return cls(pools, labels, background, data.get("meta"))

    def only(self, pool: str) -> "CapacityModel":
        """A copy holding just ``pool`` and its labels, for fitting one pool at a time."""
        return CapacityModel(
            {pool: self.pools[pool]},
            {label: r for label, r in self.labels.items() if r.pool == pool},
            {pool: self.background.get(pool, {})},
            self.meta,
        )

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh)

    @classmethod
    def load(cls, path: str) -> "CapacityModel":
        with open(path, encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))


def _quantile_sql(expr: str, q: int) -> str:
    return f"APPROX_QUANTILES({expr}, 100)[OFFSET({q})]"


def fetch_capacity(
    client: Any,
    data_source_id: int,
    since: datetime,
    until: datetime,
    min_share: float = 0.005,
    list_workers: bool = True,
) -> CapacityModel:
    """Build a model from the fxci dataset for pushes between ``since`` and ``until``.

    Pools carrying at least ``min_share`` of autoland perf runs are kept.
    Labels take the pool they ran on most and the median run time there;
    capacity is the distinct workers each pool used per month; background is
    the hourly occupancy of each pool by every run that is not autoland perf
    work; each pool's eligible time is the median from a push's decision task
    to its perf tests becoming pending, the build chain. The Taskcluster
    queue's current worker listing is kept for reference.
    """
    d0, d1 = (
        since.strftime("%Y-%m-%d"),
        (until + timedelta(days=1)).strftime("%Y-%m-%d"),
    )
    dates = f'submission_date >= "{d0}" AND submission_date < "{d1}"'
    autoland = f"""
WITH perf_tasks AS (
  SELECT task_id, submission_date, tags.label AS label, task_queue_id
  FROM `moz-fx-data-shared-prod.fxci.tasks`
  WHERE {dates} AND tags.project = 'autoland' AND {PERF_LABEL_SQL}
),
runs AS (
  SELECT task_id, submission_date,
    TIMESTAMP_DIFF(resolved, started, SECOND) AS seconds,
    TIMESTAMP_DIFF(started, scheduled, SECOND) AS wait
  FROM `moz-fx-data-shared-prod.fxci.task_runs`
  WHERE {dates} AND started IS NOT NULL AND resolved IS NOT NULL
)
"""
    label_rows = client.execute(
        data_source_id,
        autoland
        + f"""
SELECT t.label, t.task_queue_id AS pool, COUNT(*) AS runs,
  {_quantile_sql("r.seconds", 50)} AS p50_seconds
FROM perf_tasks t JOIN runs r USING (task_id, submission_date)
GROUP BY 1, 2
""",
    )
    pool_rows = client.execute(
        data_source_id,
        autoland
        + f"""
SELECT t.task_queue_id AS pool, COUNT(*) AS runs,
  {_quantile_sql("r.wait", 50)} / 60 AS wait_p50, {_quantile_sql("r.wait", 90)} / 60 AS wait_p90,
  {_quantile_sql("r.seconds", 50)} / 60 AS run_p50, {_quantile_sql("r.seconds", 90)} / 60 AS run_p90
FROM perf_tasks t JOIN runs r USING (task_id, submission_date)
GROUP BY 1
""",
    )
    total = sum(row["runs"] for row in pool_rows)
    days = max((until - since).total_seconds() / 86400, 1.0)
    pools: dict[str, PoolCapacity] = {}
    for row in pool_rows:
        if row["runs"] < min_share * total:
            continue
        pools[row["pool"]] = PoolCapacity(
            row["pool"],
            {},
            None,
            {"p50": row["wait_p50"], "p90": row["wait_p90"]},
            {"p50": row["run_p50"], "p90": row["run_p90"]},
            row["runs"] / days,
        )
    logger.info(
        "%d pools carry %.1f%% of %d autoland perf runs",
        len(pools),
        100 * sum(p.runs_per_day for p in pools.values()) * days / max(total, 1),
        total,
    )

    by_label: dict[str, collections.Counter] = collections.defaultdict(
        collections.Counter
    )
    p50: dict[tuple[str, str], list[tuple[int, float]]] = collections.defaultdict(list)
    for row in label_rows:
        if row["pool"] not in pools or row["p50_seconds"] is None:
            continue
        label = normalize_task_label(row["label"])
        by_label[label][row["pool"]] += row["runs"]
        p50[(label, row["pool"])].append((row["runs"], row["p50_seconds"] / 60))
    labels = {}
    for label, counter in by_label.items():
        pool = counter.most_common(1)[0][0]
        samples = p50[(label, pool)]
        weight = sum(n for n, _ in samples)
        labels[label] = LabelRuntime(
            pool, sum(n * minutes for n, minutes in samples) / weight
        )

    pool_list = ", ".join(f"'{name}'" for name in pools)
    joined = f"""
FROM `moz-fx-data-shared-prod.fxci.task_runs` r
JOIN `moz-fx-data-shared-prod.fxci.tasks` t USING (task_id, submission_date)
WHERE r.{dates} AND t.task_queue_id IN ({pool_list})
  AND r.started IS NOT NULL AND r.resolved IS NOT NULL
"""
    for row in client.execute(
        data_source_id,
        f"""
SELECT t.task_queue_id AS pool, FORMAT_TIMESTAMP('%Y-%m', r.started) AS month,
  COUNT(DISTINCT r.worker_id) AS workers
{joined}
GROUP BY 1, 2
""",
    ):
        pools[row["pool"]].workers_by_month[row["month"]] = int(row["workers"])

    background: dict[str, dict[int, float]] = {name: {} for name in pools}
    for row in client.execute(
        data_source_id,
        f"""
WITH pool_runs AS (
  SELECT t.task_queue_id AS pool, r.started, r.resolved
  {joined}
    AND NOT (t.tags.project = 'autoland' AND {PERF_LABEL_SQL})
)
SELECT pool, UNIX_SECONDS(hour) AS hour,
  SUM(TIMESTAMP_DIFF(LEAST(resolved, TIMESTAMP_ADD(hour, INTERVAL 1 HOUR)),
                     GREATEST(started, hour), SECOND)) / 3600 AS busy
FROM pool_runs, UNNEST(GENERATE_TIMESTAMP_ARRAY(
  TIMESTAMP_TRUNC(started, HOUR), TIMESTAMP_TRUNC(resolved, HOUR), INTERVAL 1 HOUR)) AS hour
GROUP BY 1, 2
""",
    ):
        background[row["pool"]][int(row["hour"]) // 3600] = float(row["busy"])

    for row in client.execute(
        data_source_id,
        f"""
WITH decisions AS (
  SELECT t.task_id AS group_id, r.started AS pushed
  FROM `moz-fx-data-shared-prod.fxci.tasks` t
  JOIN `moz-fx-data-shared-prod.fxci.task_runs` r USING (task_id, submission_date)
  WHERE t.{dates} AND t.tags.project = 'autoland' AND t.tags.kind = 'decision-task'
    AND r.run_id = 0 AND r.started IS NOT NULL
),
perf AS (
  SELECT t.task_group_id, t.task_queue_id AS pool, r.scheduled
  FROM `moz-fx-data-shared-prod.fxci.tasks` t
  JOIN `moz-fx-data-shared-prod.fxci.task_runs` r USING (task_id, submission_date)
  WHERE t.{dates} AND t.tags.project = 'autoland' AND {PERF_LABEL_SQL}
    AND t.task_queue_id IN ({pool_list})
    AND r.run_id = 0 AND r.reason_created = 'scheduled' AND r.scheduled IS NOT NULL
)
SELECT p.pool,
  {_quantile_sql("TIMESTAMP_DIFF(p.scheduled, d.pushed, SECOND)", 50)} / 60 AS eligible_p50
FROM perf p JOIN decisions d ON p.task_group_id = d.group_id
GROUP BY 1
""",
    ):
        if row["eligible_p50"] is not None:
            pools[row["pool"]].eligible_minutes = float(row["eligible_p50"])

    labels_by_pool = {name: build_label_for_pool(name) for name in pools}
    wanted = sorted({label for label in labels_by_pool.values() if label})
    if wanted:
        build_list = ", ".join(f"'{label}'" for label in wanted)
        shares = {}
        for row in client.execute(
            data_source_id,
            f"""
WITH decision_groups AS (
  SELECT task_id AS group_id FROM `moz-fx-data-shared-prod.fxci.tasks`
  WHERE {dates} AND tags.project = 'autoland' AND tags.kind = 'decision-task'
),
perf_counts AS (
  SELECT task_group_id AS group_id, COUNT(*) AS perf_tasks
  FROM `moz-fx-data-shared-prod.fxci.tasks`
  WHERE {dates} AND tags.project = 'autoland' AND {PERF_LABEL_SQL}
  GROUP BY 1
),
partial AS (
  SELECT g.group_id FROM decision_groups g LEFT JOIN perf_counts p USING (group_id)
  WHERE COALESCE(p.perf_tasks, 0) < {FULL_SUITE_MIN_LABELS}
),
builds AS (
  SELECT DISTINCT task_group_id AS group_id, tags.label AS label
  FROM `moz-fx-data-shared-prod.fxci.tasks`
  WHERE {dates} AND tags.project = 'autoland' AND tags.kind = 'build'
    AND tags.label IN ({build_list})
)
SELECT b.label, COUNT(DISTINCT b.group_id) AS with_build,
  (SELECT COUNT(*) FROM partial) AS pushes
FROM partial JOIN builds b USING (group_id)
GROUP BY 1
""",
        ):
            if row["pushes"]:
                shares[row["label"]] = row["with_build"] / row["pushes"]
        for name, pool in pools.items():
            build_label = labels_by_pool[name]
            if build_label in shares:
                pool.build_share = shares[build_label]

    if list_workers:
        for pool in pools.values():
            pool.listed_workers = list_pool_workers(pool.name)

    meta = {
        "since": since.isoformat(),
        "until": until.isoformat(),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "autoland_perf_runs": total,
        "labels": len(labels),
    }
    return CapacityModel(pools, labels, background, meta)


def build_label_for_pool(pool: str) -> str | None:
    """The shippable build the perf tests of ``pool`` run against, by platform."""
    name = pool.lower()
    if "android" in name or "autophone" in name or "bitbar" in name or "lambda" in name:
        return "build-android-aarch64-shippable/opt"
    if "linux" in name:
        return "build-linux64-shippable/opt"
    if "win" in name:
        return "build-win64-shippable/opt"
    if "osx" in name or "macos" in name:
        return (
            "build-macosx64-aarch64-shippable/opt"
            if "-m" in name.rsplit("/", 1)[-1]
            else "build-macosx64-shippable/opt"
        )
    return None


def build_present(push_id: int, pool: str, share: float) -> bool:
    """Whether a push the schedule ran nothing on has the pool's build, by a stable draw."""
    digest = hashlib.blake2b(f"{push_id}:{pool}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big") / 2**64 < share


def list_pool_workers(pool: str) -> int | None:
    """Workers the Taskcluster queue lists for ``pool`` now, quarantined ones excluded."""
    provisioner, worker_type = pool.split("/", 1)
    url = (
        f"{TASKCLUSTER_ROOT}/queue/v1/provisioners/{urllib.parse.quote(provisioner)}"
        f"/worker-types/{urllib.parse.quote(worker_type)}/workers?limit=1000"
    )
    try:
        with urllib.request.urlopen(url, timeout=60) as fh:
            workers = json.load(fh).get("workers", [])
    except Exception as exc:  # the listing is a reference, not an input
        logger.warning("Could not list workers of %s: %s", pool, exc)
        return None
    return sum(1 for worker in workers if not worker.get("quarantineUntil"))


@dataclass(frozen=True)
class CiSettings:
    """How a schedule's runs go through CI: the model and the fixed delays.

    ``build_minutes`` is the push-to-eligible time for pools the model has no
    measurement for; measured pools use their own ``eligible_minutes``.
    """

    model: CapacityModel
    build_minutes: float = DEFAULT_BUILD_MINUTES
    backfill: str = "all"
    sheriff_delay_minutes: float = 0.0
    capacity_scale: float = 1.0
    # Keep every run's queue wait, for validation; off for optimization.
    record_waits: bool = False
    # When the alert that starts the sheriff's backfills is raised:
    # ``fore_window`` once FORE_WINDOW runs after the culprit have completed,
    # as Perfherder does today; ``retrigger`` once the first data point has
    # been confirmed by ``confirm_retriggers`` more runs of the detecting
    # label on the detecting push; ``detection`` at the first data point.
    alerting: str = "fore_window"
    confirm_retriggers: int = 12
    # Whether a backfill on a push the schedule ran nothing on finds a build:
    # ``measured`` draws it from the pool's build share, ``always`` builds.
    backfill_builds: str = "measured"


@dataclass
class CiResult:
    """When each regression was detected and identified once CI is accounted for.

    ``detect_time`` is the first completion among the regressed labels' runs
    on their detecting pushes, or the median completion of the detecting
    batch when the regression names no label. ``surface_time`` is when the
    alert was raised under the settings' alerting mode, which is when the
    sheriff is put to work. ``identify_time`` is when both the culprit push
    and the push before it had data for one regressed label, and no earlier
    than the sheriff acting on the alert, the sheriff delay after it; ``None``
    when nothing produced that data. ``backfill_jobs`` counts the runs
    those backfills took and ``confirm_jobs`` the confirming retriggers, per
    regression. ``waits`` holds the queue wait in minutes of every scheduled
    run, per pool, and ``utilization`` the share of the window each pool was
    busy.
    """

    detect_time: dict[int, datetime]
    surface_time: dict[int, datetime]
    identify_time: dict[int, datetime | None]
    backfill_jobs: dict[int, int]
    confirm_jobs: dict[int, int]
    waits: dict[str, list[float]]
    backfill_waits: dict[str, list[float]]
    utilization: dict[str, float]
    jobs: int
    unknown_labels: int


_ARRIVAL, _TICK, _COMPLETION = 0, 1, 2
_SCHEDULED, _BACKFILL, _CONFIRM = 0, 1, 2


def simulate_ci(
    schedule: Schedule,
    timeline: Timeline,
    detections: list[Detection],
    settings: CiSettings,
) -> CiResult:
    """Replay the schedule's runs, the alerts and the backfills they trigger through the pools."""
    model = settings.model
    build = timedelta(minutes=settings.build_minutes)
    offsets = {
        name: timedelta(minutes=pool.eligible_minutes)
        if pool.eligible_minutes is not None
        else build
        for name, pool in model.pools.items()
    }
    sheriff = timedelta(minutes=settings.sheriff_delay_minutes)
    scale = settings.capacity_scale
    pushes = timeline.pushes

    # Completion times are kept only where a detection, an alert or an
    # identification needs them: a dense schedule has millions of runs.
    # Every label of a regression is waited for; the first to complete
    # detects it, and the first to reach its alert raises it.
    on_detect: dict[tuple[int, str | None], list[Detection]] = collections.defaultdict(
        list
    )
    on_surface: dict[tuple[int, str | None], list[Detection]] = collections.defaultdict(
        list
    )
    needed: set[tuple[int, str]] = set()
    median_needed: set[int] = set()
    for detection in detections:
        for candidate in detection.candidates():
            if candidate.detected_index is None:
                continue
            on_detect[(candidate.detected_index, candidate.label)].append(candidate)
            if (
                settings.alerting == "fore_window"
                and candidate.surfaced_index is not None
            ):
                on_surface[(candidate.surfaced_index, candidate.label)].append(
                    candidate
                )
        if detection.detected_index is None:
            continue
        culprit = detection.regression.push_index
        previous = _previous_built(pushes, culprit)
        for label in detection.regression.labels:
            needed.add((culprit, label))
            if previous >= 0:
                needed.add((previous, label))
    for index, waited_label in list(on_detect) + list(on_surface):
        if waited_label is None:
            median_needed.add(index)
        else:
            needed.add((index, waited_label))
    # Labels a batch whose suite is only borrowed is credited with running:
    # the ones an alert names, which the dropped jobs would have shown.
    unrecorded: dict[int, set[str]] = collections.defaultdict(set)
    for index, label in needed:
        if timeline.known_suite_at(index) is None:
            unrecorded[index].add(label)

    def batch_ran(index: int, label: str) -> bool:
        suite = timeline.known_suite_at(index)
        return suite is None or label in suite

    unknown = 0
    jobs = 0
    batch_size: dict[int, int] = {}

    def arrivals():
        """The schedule's runs in eligible order.

        Pushes are in time order and eligible times differ by pool, so runs
        are buffered until no later push can precede them.
        """
        nonlocal unknown, jobs
        buffer: list[tuple[datetime, str, float, int, str]] = []
        for push in pushes:
            decision = schedule.decisions[push.index]
            if decision is None:
                continue
            labels = timeline.suite_at(push.index) if decision == ALL else decision
            if decision == ALL and push.index in unrecorded:
                labels = labels | unrecorded[push.index]
            runs = []
            for label in labels:
                pool = model.pool_of(label)
                if pool is None or pool not in model.pools:
                    unknown += 1
                    continue
                runs.append(
                    (
                        push.time + offsets[pool],
                        pool,
                        model.minutes_of(label, pool),
                        push.index,
                        label,
                    )
                )
            batch_size[push.index] = len(runs)
            jobs += len(runs)
            buffer.extend(runs)
            buffer.sort()
            while buffer and buffer[0][0] <= push.time:
                yield buffer.pop(0)
        buffer.sort()
        yield from buffer

    done: dict[tuple[int, str], datetime] = {}
    detect_time: dict[int, datetime] = {}
    surface_time: dict[int, datetime] = {}
    # The candidate whose run completed first, by regression.
    caught: dict[int, Detection] = {}
    requested: set[tuple[int, str]] = set()
    backfill_jobs: dict[int, int] = collections.Counter()
    confirm_jobs: dict[int, int] = collections.Counter()
    confirm_left: dict[int, int] = {}
    waits: dict[str, list[float]] = {pool: [] for pool in model.pools}
    backfill_waits: dict[str, list[float]] = {pool: [] for pool in model.pools}
    busy_seconds: dict[str, float] = {pool: 0.0 for pool in model.pools}
    queues: dict[str, collections.deque] = {
        pool: collections.deque() for pool in model.pools
    }
    running: dict[str, int] = {pool: 0 for pool in model.pools}
    avail: dict[str, int] = {}
    batch_done: dict[int, int] = collections.Counter()
    events: list[tuple[datetime, int, int, Any]] = []
    sequence = 0

    def push_event(time: datetime, kind: int, payload: Any) -> None:
        nonlocal sequence
        sequence += 1
        heapq.heappush(events, (time, kind, sequence, payload))

    if pushes:
        tick = pushes[0].time.replace(minute=0, second=0, microsecond=0)
        last = (
            pushes[-1].time + max(offsets.values(), default=build) + timedelta(hours=48)
        )
        while tick <= last:
            push_event(tick, _TICK, None)
            tick += timedelta(hours=1)
        for pool in model.pools:
            avail[pool] = model.available(pool, pushes[0].time, scale)

    def dispatch(pool: str, now: datetime) -> None:
        queue = queues[pool]
        while queue and running[pool] < avail[pool]:
            eligible, minutes, index, label, kind, ref = queue.popleft()
            running[pool] += 1
            busy_seconds[pool] += minutes * 60
            if settings.record_waits:
                (waits if kind == _SCHEDULED else backfill_waits)[pool].append(
                    (now - eligible).total_seconds() / 60
                )
            push_event(
                now + timedelta(minutes=minutes),
                _COMPLETION,
                (pool, index, label, kind, ref),
            )

    def backfill(detection: Detection, time: datetime) -> None:
        """Run the regressed labels on the window's pushes without data, from ``time``.

        A push the schedule ran nothing on only has a shippable build when
        other tests on it needed one, drawn from the pool's measured share;
        otherwise the backfill builds first and waits the pool's eligible time.
        A run another alert already requested is not requested again; its
        result serves both.
        """
        summary_id = detection.regression.summary_id
        if settings.backfill == "none" or not detection.regression.labels:
            return
        assert detection.detected_index is not None
        start = (
            detection.previous_index + 1
            if detection.previous_index is not None
            else max(0, detection.detected_index - MAX_BACKFILL_PUSHES)
        )
        trigger = time + sheriff
        for label in sorted(detection.regression.labels):
            pool = model.pool_of(label)
            if pool is None or pool not in model.pools:
                continue
            minutes = model.minutes_of(label, pool)
            for index in range(start, detection.detected_index):
                push = pushes[index]
                decision = schedule.decisions[index]
                if (
                    push.dontbuild
                    or (decision == ALL and batch_ran(index, label))
                    or (decision and label in decision)
                    or (index, label) in requested
                ):
                    continue
                requested.add((index, label))
                backfill_jobs[summary_id] += 1
                share = model.pools[pool].build_share
                built = decision is not None or (
                    settings.backfill_builds == "measured"
                    and share is not None
                    and build_present(push.push_id, pool, share)
                )
                eligible = (
                    max(trigger, push.time + offsets[pool])
                    if built
                    else trigger + offsets[pool]
                )
                push_event(
                    eligible, _ARRIVAL, (pool, minutes, index, label, _BACKFILL, None)
                )

    def alert(detection: Detection, time: datetime) -> None:
        summary_id = detection.regression.summary_id
        if summary_id in surface_time:
            return
        surface_time[summary_id] = time
        backfill(detection, time)

    def detected(detection: Detection, time: datetime) -> None:
        summary_id = detection.regression.summary_id
        if summary_id in detect_time:
            return
        detect_time[summary_id] = time
        caught[summary_id] = detection
        if settings.alerting == "detection":
            alert(detection, time)
        elif settings.alerting == "retrigger":
            label = detection.label
            pool = model.pool_of(label) if label is not None else None
            if label is None or pool is None or pool not in model.pools:
                alert(detection, time)
                return
            assert detection.detected_index is not None
            confirm_left[summary_id] = settings.confirm_retriggers
            confirm_jobs[summary_id] = settings.confirm_retriggers
            minutes = model.minutes_of(label, pool)
            for _ in range(settings.confirm_retriggers):
                push_event(
                    time,
                    _ARRIVAL,
                    (
                        pool,
                        minutes,
                        detection.detected_index,
                        label,
                        _CONFIRM,
                        summary_id,
                    ),
                )
            if settings.confirm_retriggers == 0:
                alert(detection, time)

    def completed_scheduled(index: int, label: str, now: datetime) -> None:
        key = (index, label)
        for detection in on_detect.pop(key, ()):
            detected(detection, now)
        for detection in on_surface.pop(key, ()):
            alert(detection, now)
        if index in median_needed:
            batch_done[index] += 1
            if batch_done[index] == math.ceil(batch_size[index] / 2):
                for detection in on_detect.pop((index, None), ()):
                    detected(detection, now)
                for detection in on_surface.pop((index, None), ()):
                    alert(detection, now)

    stream = arrivals()
    pending = next(stream, None)
    while pending is not None or events:
        if events and (pending is None or events[0][0] <= pending[0]):
            now, kind, _, payload = heapq.heappop(events)
            if kind == _TICK:
                for pool in model.pools:
                    avail[pool] = model.available(pool, now, scale)
                    dispatch(pool, now)
            elif kind == _ARRIVAL:
                pool, minutes, index, label, run_kind, ref = payload
                queues[pool].append((now, minutes, index, label, run_kind, ref))
                dispatch(pool, now)
            else:
                pool, index, label, run_kind, ref = payload
                running[pool] -= 1
                key = (index, label)
                if (
                    run_kind != _CONFIRM
                    and key in needed
                    and (key not in done or now < done[key])
                ):
                    done[key] = now
                if run_kind == _SCHEDULED:
                    completed_scheduled(index, label, now)
                elif run_kind == _CONFIRM:
                    confirm_left[ref] -= 1
                    if confirm_left[ref] == 0:
                        alert(caught[ref], now)
                dispatch(pool, now)
        else:
            assert pending is not None
            eligible, pool, minutes, index, label = pending
            queues[pool].append((eligible, minutes, index, label, _SCHEDULED, None))
            dispatch(pool, eligible)
            pending = next(stream, None)

    identify_time: dict[int, datetime | None] = {}
    for detection in detections:
        summary_id = detection.regression.summary_id
        if summary_id in detect_time:
            identify_time[summary_id] = (
                _identify(detection, pushes, done, surface_time[summary_id] + sheriff)
                if summary_id in surface_time
                else None
            )

    span = (
        (pushes[-1].time - pushes[0].time).total_seconds() if len(pushes) > 1 else 1.0
    )
    utilization = {}
    for pool in model.pools:
        workers = statistics.fmean(
            [
                model.pools[pool].workers_at(pushes[0].time),
                model.pools[pool].workers_at(pushes[-1].time),
            ]
        )
        utilization[pool] = busy_seconds[pool] / max(span * workers, 1.0)
    return CiResult(
        detect_time,
        surface_time,
        identify_time,
        dict(backfill_jobs),
        dict(confirm_jobs),
        waits,
        backfill_waits,
        utilization,
        jobs,
        unknown,
    )


def _previous_built(pushes: list, index: int) -> int:
    """The nearest push before ``index`` that was built, -1 when there is none."""
    previous = index - 1
    while previous >= 0 and pushes[previous].dontbuild:
        previous -= 1
    return previous


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))] if ordered else 0.0


def calibrate_scales(
    model: CapacityModel,
    schedule: Schedule,
    timeline: Timeline,
    detections: list[Detection],
    settings: CiSettings,
    target: str = "p50",
    low: float = 0.2,
    steps: int = 7,
) -> dict[str, dict[str, float]]:
    """Fit each pool's ``scale`` so the replayed waits match the observed ones.

    The schedule should be production's, since the observed waits are those
    of autoland's actual perf runs, batches and backfills alike. Pools are
    independent given the schedule, so each is fitted alone by bisection on
    the median (or ``p90``) wait of all its replayed runs. The pool's scale is
    set in place; the summary returned holds the fitted scale and both waits.
    """
    quantile = 0.5 if target == "p50" else 0.9
    settings = replace(settings, record_waits=True)
    summary = {}
    for name, pool in model.pools.items():
        observed = pool.observed_wait_minutes.get(target)
        if observed is None:
            continue
        alone = model.only(name)
        args = (schedule, timeline, detections, settings, quantile)
        lo, hi = low, 1.0
        wait_hi = _wait_at(alone, name, hi, *args)
        if wait_hi >= observed:
            fitted, simulated = hi, wait_hi
        else:
            for _ in range(steps):
                mid = (lo + hi) / 2
                if _wait_at(alone, name, mid, *args) > observed:
                    lo = mid
                else:
                    hi = mid
            fitted, simulated = hi, _wait_at(alone, name, hi, *args)
        pool.scale = fitted
        summary[name] = {"scale": fitted, "observed": observed, "simulated": simulated}
        logger.info(
            "%s: scale %.3f, %s wait %.1f min replayed vs %.1f observed",
            name,
            fitted,
            target,
            simulated,
            observed,
        )
    return summary


def _identify(
    detection: Detection,
    pushes: list,
    done: dict[tuple[int, str], datetime],
    acting: datetime,
) -> datetime | None:
    """When the culprit and the push before it both had data for one regressed label.

    Never before ``acting``, when the sheriff turns to the alert: data the
    schedule happened to produce earlier identifies nothing until then.
    """
    regression = detection.regression
    if not regression.labels:
        return acting
    culprit = regression.push_index
    previous = _previous_built(pushes, culprit)
    best: datetime | None = None
    for label in regression.labels:
        at_culprit = done.get((culprit, label))
        at_previous = done.get((previous, label)) if previous >= 0 else None
        if at_culprit is None or (previous >= 0 and at_previous is None):
            continue
        when = max(at_culprit, at_previous) if at_previous else at_culprit
        if best is None or when < best:
            best = when
    return None if best is None else max(best, acting)


def _wait_at(
    alone: CapacityModel,
    pool: str,
    scale: float,
    schedule: Schedule,
    timeline: Timeline,
    detections: list[Detection],
    settings: CiSettings,
    quantile: float,
) -> float:
    alone.pools[pool].scale = scale
    result = simulate_ci(schedule, timeline, detections, replace(settings, model=alone))
    return _percentile(result.waits[pool] + result.backfill_waits[pool], quantile)
