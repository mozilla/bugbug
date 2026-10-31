# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Scheduling strategies, as decisions taken push by push.

Each strategy mirrors an optimization strategy of Firefox's task graph
(``taskcluster/gecko_taskgraph/optimize/perf_batching.py`` and
``util/backstop.py``), reduced to the arithmetic of the decision: the task
graph machinery that reloads state across shadow scheduler runs is not needed
here, since the replay sees the whole history in order. ``decide`` returns
the labels to run on a push: :data:`ALL` for the whole suite, a set for part
of it, ``None`` for nothing.
"""

import collections
import logging
import math
from datetime import timedelta
from typing import Any, ClassVar, Iterable, Literal

import numpy as np

from bugbug.perf.simulation.timeline import Push

logger = logging.getLogger(__name__)

ALL: Literal["all"] = "all"
Decision = frozenset[str] | Literal["all"] | None

# As in perf_batching.py: risk scores are capped below 1 so log(1 - risk) is finite.
MAX_RISK = 1 - 1e-9
# A linear risk threshold is a sum of scores, so its useful range depends on
# the scores: from one commit's worth up to what this many hours of pushes
# accumulate, beyond which a batch would be rarer than any schedule worth
# considering.
LINEAR_THRESHOLD_HOURS = 48

SearchSpace = dict[str, tuple[float, float, bool]]


class Strategy:
    """Base class: a named family with parameters and per-push state."""

    family: ClassVar[str] = ""
    # Parameter name -> (low, high, log scale) for optuna's search space.
    # Integer bounds make an integer parameter.
    search_space: ClassVar[SearchSpace] = {}

    def __init__(self, **params: Any) -> None:
        self.params = params
        self.reset()

    @property
    def name(self) -> str:
        rendered = ",".join(f"{k}={v}" for k, v in sorted(self.params.items()))
        return f"{self.family}({rendered})" if rendered else self.family

    def reset(self) -> None:
        """Forget every push seen so far."""

    def observe(self, push: Push) -> None:
        """Account for a push, whether or not a decision is taken on it.

        Called for every push in order, before ``decide``; a push the decision
        task skips entirely (DONTBUILD) is observed but never decided.
        """

    def decide(self, push: Push) -> Decision:
        raise NotImplementedError

    @classmethod
    def search_space_for(cls, pushes: Iterable[Push]) -> SearchSpace:
        """The search space to tune on ``pushes``; the class's one unless data sets it."""
        return dict(cls.search_space)

    @classmethod
    def sweep(cls, space: SearchSpace, n_points: int) -> dict[str, list[Any]] | None:
        """Every value a one-parameter family is tried at, None for other families.

        An integer parameter takes every integer in its bounds; a real one
        ``n_points`` values spaced evenly, or evenly in log, between them.
        """
        if len(space) != 1:
            return None
        ((name, (low, high, log)),) = space.items()
        if isinstance(low, int) and isinstance(high, int):
            return {name: list(range(low, high + 1))}
        values = (
            np.geomspace(low, high, n_points)
            if log
            else np.linspace(low, high, n_points)
        )
        return {name: [float(value) for value in values]}

    @classmethod
    def suggest(cls, trial: Any, space: SearchSpace | None = None) -> dict[str, Any]:
        """Draw parameters from an optuna trial over ``space`` (default ``search_space``)."""
        params = {}
        for name, (low, high, log) in (space or cls.search_space).items():
            if isinstance(low, int) and isinstance(high, int):
                params[name] = trial.suggest_int(name, low, high, log=log)
            else:
                params[name] = trial.suggest_float(name, low, high, log=log)
        return params


def _pushlog_or_index(push: Push, strategy: Strategy) -> int:
    """The pushlog id the task graph's push-count rules use, else the position."""
    if push.pushlog_id is not None:
        return push.pushlog_id
    if not getattr(strategy, "_warned_pushlog", False):
        logger.warning(
            "%s: pushes without pushlog id, counting positions instead", strategy.name
        )
        strategy._warned_pushlog = True  # type: ignore[attr-defined]
    return push.index


class Production(Strategy):
    """What autoland schedules today.

    The whole suite runs on backstop pushes: the first push, then whenever
    ``push_interval`` pushes or ``time_interval_hours`` have passed since the
    last backstop, both counted from that backstop. ``expanded_labels`` also
    run on every ``expanded_interval``-th pushlog id, as tier 1 raptor tests
    against non-shippable builds do.
    """

    family = "production"

    def __init__(
        self,
        push_interval: int = 20,
        time_interval_hours: float = 4.0,
        expanded_interval: int = 10,
        expanded_labels: frozenset[str] = frozenset(),
    ) -> None:
        self.expanded_labels = frozenset(expanded_labels)
        super().__init__(
            push_interval=push_interval,
            time_interval_hours=time_interval_hours,
            expanded_interval=expanded_interval,
        )

    @property
    def name(self) -> str:
        return "production"

    def reset(self) -> None:
        self.last_backstop: Push | None = None

    def is_backstop(self, push: Push) -> bool:
        if self.last_backstop is None:
            return True
        pushes = _pushlog_or_index(push, self) - _pushlog_or_index(
            self.last_backstop, self
        )
        if pushes >= self.params["push_interval"]:
            return True
        hours = (push.time - self.last_backstop.time).total_seconds() / 3600
        return hours >= self.params["time_interval_hours"]

    def decide(self, push: Push) -> Decision:
        if self.is_backstop(push):
            self.last_backstop = push
            return ALL
        if (
            self.expanded_labels
            and _pushlog_or_index(push, self) % self.params["expanded_interval"] == 0
        ):
            return self.expanded_labels
        return None


class RecordedProduction(Strategy):
    """The schedule autoland actually ran, read from the pushes.

    The whole suite on the pushes where it ran, and ``expanded_labels`` on
    the pushes where they ran.

    Jobs on other pushes, sheriff backfills and retriggers, are left out, so
    the schedule is production's scheduling alone. Production's backstop
    decisions depend on which decision task finished first, so this is the
    truer baseline where history exists; :class:`Production` is the rule.
    """

    family = "production_recorded"

    def __init__(self, expanded_labels: frozenset[str] = frozenset()) -> None:
        self.expanded_labels = frozenset(expanded_labels)
        super().__init__()

    @property
    def name(self) -> str:
        return "production_recorded"

    def decide(self, push: Push) -> Decision:
        if push.full_suite:
            return ALL
        return (push.ran_labels & self.expanded_labels) or None


class FixedInterval(Strategy):
    """The whole suite once ``push_interval`` pushes have been made since the last batch.

    Pushes are counted by pushlog id, like the backstop's push interval and
    ``SkipUnlessPushesSinceLastBatch`` in the task graph, so a batch due on a
    push nothing runs on is deferred to the next push rather than lost.
    """

    family = "fixed_interval"
    search_space = {"push_interval": (2, 200, True)}

    def __init__(self, push_interval: int) -> None:
        super().__init__(push_interval=int(push_interval))

    def reset(self) -> None:
        self.last_batch: Push | None = None

    def decide(self, push: Push) -> Decision:
        if self.last_batch is None or (
            _pushlog_or_index(push, self) - _pushlog_or_index(self.last_batch, self)
            >= self.params["push_interval"]
        ):
            self.last_batch = push
            return ALL
        return None


class TimeSinceBatch(Strategy):
    """The whole suite once ``hours`` have passed since the last batch."""

    family = "time_since_batch"
    search_space = {"hours": (0.25, 48.0, True)}

    def __init__(self, hours: float) -> None:
        super().__init__(hours=float(hours))

    def reset(self) -> None:
        self.last_batch: Push | None = None

    def decide(self, push: Push) -> Decision:
        if self.last_batch is None or (
            (push.time - self.last_batch.time).total_seconds()
            >= self.params["hours"] * 3600
        ):
            self.last_batch = push
            return ALL
        return None


class RiskThreshold(Strategy):
    """The whole suite once the risk of the commits since the last batch reaches a threshold.

    Each scored commit contributes the hazard ``-ln(1 - r)``, plus
    ``aging_rate`` per hour it has waited. With ``product`` aggregation the
    batch is due when the probability that at least one commit regressed,
    ``1 - exp(-sum of hazards)``, reaches the threshold; with ``linear``
    aggregation when the sum of the aged scores ``1 - exp(-hazard)`` does.
    Commits the predictor skips are left out.
    """

    family = "risk_threshold"
    aggregation: ClassVar[str] = "product"
    aged: ClassVar[bool] = False

    def __init__(self, threshold: float, aging_rate: float = 0.0) -> None:
        if self.aggregation == "product" and not 0 < threshold < 1:
            raise ValueError("a product aggregation threshold must be in (0, 1)")
        params: dict[str, Any] = {"threshold": float(threshold)}
        if self.aged:
            params["aging_rate"] = float(aging_rate)
        self.aging_rate = float(aging_rate) if self.aged else 0.0
        super().__init__(**params)

    @classmethod
    def search_space_for(cls, pushes: Iterable[Push]) -> SearchSpace:
        """Bound a linear threshold by the scores of ``pushes``.

        A linear threshold is a sum of scores, whose scale depends on the
        model and on calibration, so the class bounds only hold the aging
        rate. The threshold runs from the 1st percentile of a single commit's
        score, which fires on nearly every push, to the 99th percentile of the
        score accumulated over ``LINEAR_THRESHOLD_HOURS`` of pushes. Product
        thresholds are probabilities and keep their fixed bounds.
        """
        space = dict(cls.search_space)
        if cls.aggregation != "linear":
            return space
        risks: list[float] = []
        sums: list[float] = []
        recent: collections.deque[tuple[Any, float]] = collections.deque()
        total = 0.0
        for push in pushes:
            scored = [c.risk for c in push.commits if c.scored and c.risk is not None]
            risks.extend(scored)
            total += sum(scored)
            recent.append((push.time, sum(scored)))
            while push.time - recent[0][0] > timedelta(hours=LINEAR_THRESHOLD_HOURS):
                total -= recent.popleft()[1]
            sums.append(total)
        if not risks:
            return space
        low = max(float(np.percentile(risks, 1)), 1e-4)
        high = max(float(np.percentile(sums, 99)), 10 * low)
        space["threshold"] = (low, high, True)
        return space

    def reset(self) -> None:
        self.last_batch: Push | None = None
        self.pending: list[Push] = []

    def observe(self, push: Push) -> None:
        self.pending.append(push)

    def score(self, now: Push) -> float:
        hazards = []
        for push in self.pending:
            waited = (now.time - push.time).total_seconds() / 3600
            for commit in push.commits:
                if not commit.scored:
                    continue
                if commit.risk is None:
                    raise ValueError(f"Commit {commit.node} has no risk score")
                hazards.append(
                    -math.log1p(-min(commit.risk, MAX_RISK)) + self.aging_rate * waited
                )
        if self.aggregation == "product":
            return -math.expm1(-sum(hazards))
        return sum(-math.expm1(-hazard) for hazard in hazards)

    def decide(self, push: Push) -> Decision:
        due = self.last_batch is None or self.score(push) >= self.params["threshold"]
        if due:
            self.last_batch = push
            self.pending = []
            return ALL
        return None


class Rasb(RiskThreshold):
    """Risk-aware stream batching: product aggregation, no aging."""

    family = "rasb"
    aggregation = "product"
    search_space = {"threshold": (0.02, 0.98, False)}


class RasbLa(RiskThreshold):
    """RASB with linear aggregation."""

    family = "rasb_la"
    aggregation = "linear"
    # Placeholder bounds; search_space_for derives them from the scores.
    search_space = {"threshold": (0.05, 20.0, True)}


class Rapb(RiskThreshold):
    """Risk-aged priority batching: product aggregation with aging."""

    family = "rapb"
    aggregation = "product"
    aged = True
    search_space = {"threshold": (0.02, 0.98, False), "aging_rate": (0.0005, 0.5, True)}


class RapbLa(RiskThreshold):
    """RAPB with linear aggregation."""

    family = "rapb_la"
    aggregation = "linear"
    aged = True
    # Placeholder threshold bounds; search_space_for derives them from the scores.
    search_space = {"threshold": (0.05, 20.0, True), "aging_rate": (0.0005, 0.5, True)}


FAMILIES: dict[str, type[Strategy]] = {
    cls.family: cls
    for cls in (
        Production,
        RecordedProduction,
        FixedInterval,
        TimeSinceBatch,
        Rasb,
        RasbLa,
        Rapb,
        RapbLa,
    )
}
RISK_FAMILIES = frozenset(cls.family for cls in (Rasb, RasbLa, Rapb, RapbLa))

# The shadow schedulers' current parameters, from the ``experimental`` class
# in gecko_taskgraph/optimize/__init__.py, keyed by shadow scheduler name.
# The in-tree shadow schedulers' parameters (``experimental`` in
# gecko_taskgraph.optimize), as tuned on calibrated scores on 2026-10-31.
PRESETS: dict[str, tuple[str, dict[str, Any]]] = {
    "perf_fixed_batch": ("fixed_interval", {"push_interval": 18}),
    "perf_time_batch": ("time_since_batch", {"hours": 3}),
    "perf_rasb": ("rasb", {"threshold": 0.28}),
    "perf_rasb_la": ("rasb_la", {"threshold": 0.32}),
    "perf_rapb": ("rapb", {"threshold": 0.6, "aging_rate": 0.02}),
    "perf_rapb_la": ("rapb_la", {"threshold": 0.55, "aging_rate": 0.01}),
}


def make_strategy(family: str, params: dict[str, Any]) -> Strategy:
    if family not in FAMILIES:
        raise ValueError(
            f"Unknown strategy family {family!r}; one of {sorted(FAMILIES)}"
        )
    return FAMILIES[family](**params)


def parse_strategy(spec: str) -> Strategy:
    """Build a strategy from ``family:key=value,...``, ``preset:name`` or ``production``.

    Numbers are parsed as integers when they look like one, else floats.
    """
    family, _, rendered = spec.partition(":")
    if family == "preset":
        if rendered not in PRESETS:
            raise ValueError(f"Unknown preset {rendered!r}; one of {sorted(PRESETS)}")
        family, preset_params = PRESETS[rendered]
        return make_strategy(family, dict(preset_params))
    params: dict[str, Any] = {}
    for item in filter(None, rendered.split(",")):
        key, _, value = item.partition("=")
        try:
            params[key.strip()] = int(value)
        except ValueError:
            params[key.strip()] = float(value)
    return make_strategy(family, params)
