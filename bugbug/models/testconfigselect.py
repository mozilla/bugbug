# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Selection of the configurations to run the selected test groups on.

The model predicts, for a group that regressed, the probability that it fails on a config given that
the configs already selected for it passed: P(c fails | group regressed, configs in S passed). This
is the quantity a greedy selection needs: the probability of catching the regression with S + c is
cov(S) + (1 - cov(S)) * P(c | S passed). Unlike pairwise "failing together" statistics or a model of
P(c fails | group regressed) alone, it learns how configs fail together (e.g. that a config of the
same platform as one that passed rarely adds anything).

Training rows come from past regressions: their configs are walked in a few orders (cheapest first,
and random), and at each step every config not walked yet is a row labelled with whether it failed,
until the first failing config.

Configs are selected for all the groups of a push at once (see TestConfigSelectModel.select_configs),
among the configs of the tasks in the current task graph that run each group (see live_candidates):
a config is added to a group as long as P(group regresses) times the gain in the probability of
catching its regression, divided by the cost, is at least a threshold. Configs already used by other
groups of the push are cheaper, as their tasks run anyway.
"""

import collections
import fnmatch
import functools
import logging
import math
import os
import random
import re
from typing import Any, Collection, Iterable, Iterator, Mapping, NamedTuple, Sequence

import numpy as np
import xgboost
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction import DictVectorizer
from sklearn.pipeline import Pipeline
from tabulate import tabulate

from bugbug import (
    commit_features,
    db,
    repository,
    test_scheduling,
    test_scheduling_features,
    utils,
)
from bugbug.model import Model

logger = logging.getLogger(__name__)

TRAIN_PUSH_FRACTION = 0.9
# Orders in which the configs of a regression are walked to generate training rows: cheapest first
# (as the cost-aware selection tends to do) and random ones.
ORDERINGS = 3
# Candidate configs sampled as rows at each step of an ordering.
CANDIDATES_PER_STEP = 10
# A few pushes break many groups at once: weight their rows down, as for the group selection model.
POSITIVE_WEIGHT_K = 5
# Running a config at all has a fixed cost (setup), on top of the cost of each group run on it: about the cost of
# running one more group.
SETUP_COST_MULTIPLIER = 1
MAX_STEPS = 14
EVALUATION_THRESHOLDS = (0.05, 0.02, 0.01, 0.005, 0.003, 0.002, 0.001, 0.0005)


def config_cost(config: str) -> float:
    from bugbug.models.testselect import _get_cost

    return _get_cost(config)


@functools.cache
def _suite(group: str) -> str:
    return test_scheduling_features.ManifestSuite()({"name": group}, None) or "other"


class ConfigHistory:
    """How often groups failed on configs in past regressions (time-ordered target encoding).

    Counts are kept for the group, its directory and its suite, combined with the config and with
    each of its tokens (so a new platform version starts from the rates of its tokens, like its
    platform family). A regression's outcomes come from pushes up to CONFIG_SELECT_MAX_LATER pushes
    after it, so it's only counted that many pushes later.
    """

    def __init__(self, delay: int = test_scheduling.CONFIG_SELECT_MAX_LATER) -> None:
        self.delay = delay
        self.failures: collections.Counter = collections.Counter()
        self.runs: collections.Counter = collections.Counter()
        self.group_regressions: collections.Counter = collections.Counter()
        self.group_all_failed: collections.Counter = collections.Counter()
        self.group_failed_fraction: collections.defaultdict[str, float] = (
            collections.defaultdict(float)
        )
        self.pending: collections.deque = collections.deque()

    @staticmethod
    def _config_keys(group: str, config: str) -> list[tuple[str, ...]]:
        return [
            ("group_config", group, config),
            ("directory_config", os.path.dirname(group), config),
            ("suite_config", _suite(group), config),
            ("config", config),
        ]

    @staticmethod
    def _token_keys(group: str, token: str) -> list[tuple[str, ...]]:
        return [
            ("group_token", group, token),
            ("directory_token", os.path.dirname(group), token),
            ("suite_token", _suite(group), token),
            ("token", token),
        ]

    def add(self, regression: test_scheduling.ConfigSelectRegression) -> None:
        self.pending.append(regression)

    def advance(self, push_index: int) -> None:
        """Count the regressions whose outcomes are all known at `push_index`."""
        while self.pending and self.pending[0].push_index < push_index - self.delay:
            self._count(self.pending.popleft())

    def flush(self) -> None:
        while self.pending:
            self._count(self.pending.popleft())

    def _count(self, regression: test_scheduling.ConfigSelectRegression) -> None:
        group = regression.group
        for config in regression.observed:
            failed = config in regression.failed
            keys = self._config_keys(group, config) + [
                key
                for token in test_scheduling.config_tokens(config)
                for key in self._token_keys(group, token)
            ]
            for key in keys:
                self.failures[key] += failed
                self.runs[key] += 1
        self.group_regressions[group] += 1
        self.group_all_failed[group] += regression.failed >= regression.observed
        self.group_failed_fraction[group] += len(regression.failed) / len(
            regression.observed
        )

    def _rate(self, key: tuple[str, ...], prior: float, strength: float) -> float:
        return (self.failures[key] + strength * prior) / (self.runs[key] + strength)

    def likelihood(self, group: str, config: str) -> float:
        """P(config fails | group regressed), shrunk from the suite's token rates to the group's own."""
        prior = self._rate(("token", "test"), 0.25, 1)
        token_rates = [
            self._rate(("suite_token", _suite(group), token), prior, 10)
            for token in test_scheduling.config_tokens(config)
        ]
        p = sum(token_rates) / len(token_rates) if token_rates else prior
        p = self._rate(("config", config), p, 10)
        p = self._rate(("suite_config", _suite(group), config), p, 5)
        return self._rate(("group_config", group, config), p, 3)

    def features(self, group: str, config: str) -> dict[str, float]:
        features: dict[str, float] = {}
        for key in self._config_keys(group, config):
            runs = self.runs[key]
            features[f"runs_{key[0]}"] = runs
            if runs:
                features[f"failure_rate_{key[0]}"] = self.failures[key] / runs

        token_keys = [
            self._token_keys(group, token)
            for token in test_scheduling.config_tokens(config)
        ]
        for i, kind in enumerate(
            ("group_token", "directory_token", "suite_token", "token")
        ):
            keys = [keys_of_token[i] for keys_of_token in token_keys]
            features[f"runs_{kind}"] = sum(self.runs[key] for key in keys)
            rates = [
                self.failures[key] / self.runs[key] for key in keys if self.runs[key]
            ]
            if rates:
                features[f"failure_rate_{kind}_mean"] = sum(rates) / len(rates)
                features[f"failure_rate_{kind}_max"] = max(rates)
                features[f"failure_rate_{kind}_min"] = min(rates)

        regressions = self.group_regressions[group]
        features["group_regressions"] = regressions
        if regressions:
            features["group_all_failed"] = self.group_all_failed[group] / regressions
            features["group_failed_fraction"] = (
                self.group_failed_fraction[group] / regressions
            )
        features["likelihood"] = self.likelihood(group, config)
        return features


class PatchInfo(NamedTuple):
    """The OS families hinted at by the patch's files (see test_scheduling.get_path_platform_hints)."""

    hints: Mapping[str, int]
    files_num: int

    @staticmethod
    def from_files(files: Collection[str]) -> "PatchInfo":
        return PatchInfo(test_scheduling.get_path_platform_hints(files), len(files))


NO_PATCH_INFO = PatchInfo({}, 0)


def make_test_job(
    group: str,
    config: str,
    candidates: Sequence[str],
    history_features: Mapping[str, dict[str, float]],
    selected: Sequence[str],
    patch: PatchInfo = NO_PATCH_INFO,
) -> dict[str, Any]:
    return {
        "name": group,
        "config": config,
        "history": history_features[config],
        "candidates": candidates,
        "cost": config_cost(config),
        "selected": list(selected),
        "selected_likelihoods": [history_features[c]["likelihood"] for c in selected],
        "selected_costs": [config_cost(c) for c in selected],
        "patch_hints": patch.hints,
        "patch_files_num": patch.files_num,
    }


class TrainingRow(NamedTuple):
    key: tuple
    test_job: dict[str, Any]
    label: int
    weight: float


def generate_rows(
    regressions: Sequence[test_scheduling.ConfigSelectRegression],
    history: ConfigHistory,
    patches: Mapping[int, PatchInfo] | None = None,
) -> Iterator[
    tuple[
        test_scheduling.ConfigSelectRegression,
        dict[str, dict[str, float]],
        list[TrainingRow],
    ]
]:
    """For each regression (in push order), its configs' history features and its training rows.

    The history features only use regressions before the regression's push (see ConfigHistory).
    `patches` maps push indices to information about their patch.
    """
    groups_per_push = collections.Counter(r.push_index for r in regressions)
    for regression in regressions:
        history.advance(regression.push_index)
        candidates = sorted(regression.observed)
        history_features = {
            config: history.features(regression.group, config) for config in candidates
        }
        patch = (patches or {}).get(regression.push_index, NO_PATCH_INFO)

        rng = random.Random(f"{regression.revs[0]}/{regression.group}")
        orderings = [
            sorted(candidates, key=lambda c: (config_cost(c), rng.random()))
        ] + [rng.sample(candidates, len(candidates)) for _ in range(ORDERINGS - 1)]
        weight = (
            POSITIVE_WEIGHT_K
            / max(POSITIVE_WEIGHT_K, groups_per_push[regression.push_index])
            / len(orderings)
        )

        rows = []
        for ordering_index, ordering in enumerate(orderings):
            selected: list[str] = []
            for step, walked in enumerate(ordering):
                remaining = [c for c in candidates if c not in selected]
                for config in rng.sample(
                    remaining, min(CANDIDATES_PER_STEP, len(remaining))
                ):
                    rows.append(
                        TrainingRow(
                            (
                                regression.revs[0],
                                regression.group,
                                ordering_index,
                                step,
                                config,
                            ),
                            make_test_job(
                                regression.group,
                                config,
                                candidates,
                                history_features,
                                selected,
                                patch,
                            ),
                            int(config in regression.failed),
                            weight,
                        )
                    )
                if walked in regression.failed:
                    break
                selected.append(walked)

        yield regression, history_features, rows
        history.add(regression)


def _label_without_chunk(label: str) -> str:
    parts = label.split("-")
    return "-".join(parts[:-1] if parts[-1].isdigit() else parts)


def live_candidates(
    manifests_by_task: Mapping[str, Iterable[str]], known_configs: Iterable[str]
) -> dict[str, list[str]]:
    """The configs of the tasks in the current task graph that run each group.

    `manifests_by_task` maps the labels of the tasks scheduled on the push's project to the manifests
    they run (the decision task's manifests-by-task.json, restricted to its target tasks, as it comes
    from the full task graph, which also has tasks of other projects, e.g. shippable variants). The
    configs are patterns over task labels without their chunk number (e.g.
    test-macosx1500-aarch64/debug-*-swr), as the taskgraph matches them: each task is named after
    the most specific `known_configs` pattern it matches, or after its own label without the chunk
    number if none matches (e.g. for a new platform).

    As the candidates come from the current task graph, configs that don't exist anymore (e.g. after
    a platform migration) are never selected.
    """
    patterns = sorted(
        ((len(c), c, re.compile(fnmatch.translate(c))) for c in set(known_configs)),
        reverse=True,
    )

    @functools.cache
    def config_of(label: str) -> str:
        for _, config, pattern in patterns:
            if pattern.match(label):
                return config
        return label

    result: dict[str, set[str]] = collections.defaultdict(set)
    for label, manifests in manifests_by_task.items():
        if not label.startswith("test-"):
            continue
        config = config_of(_label_without_chunk(label))
        for manifest in manifests:
            result[manifest.split(":")[0]].add(config)

    return {group: sorted(configs) for group, configs in result.items()}


class GroupCandidates(NamedTuple):
    group: str
    candidates: Sequence[str]
    # What the model needs to compute the group's probabilities (see ConfigSelector._group_state).
    state: Any
    risk: float
    # OS families the selection must include a config of.
    required_families: frozenset[str] = frozenset()


class PushCandidates(NamedTuple):
    groups: Sequence[GroupCandidates]
    patch: PatchInfo = NO_PATCH_INFO


def _plainest_cheapest(configs: Iterable[str]) -> str:
    return min(configs, key=lambda c: (config_cost(c), len(c), c))


def load_regressions(
    with_patches: bool = True,
) -> tuple[
    list[test_scheduling.ConfigSelectRegression], set[str], dict[int, PatchInfo]
]:
    """The regressions of the push data, the configs seen recently, and the patch of each regression's push."""
    regressions, known_configs = test_scheduling.get_config_select_regressions(
        lambda: db.read(test_scheduling.PUSH_DATA_CONFIG_GROUP_DB)
    )
    logger.info(
        "%d regressions on %d pushes",
        len(regressions),
        len({r.push_index for r in regressions}),
    )

    if not with_patches:
        return regressions, known_configs, {}

    revs = {rev: r.push_index for r in regressions for rev in r.revs}
    files: dict[int, list[str]] = collections.defaultdict(list)
    for commit in repository.get_commits(
        include_no_bug=True, include_backouts=True, include_ignored=True
    ):
        if commit["node"] in revs:
            files[revs[commit["node"]]].extend(commit["files"])
    patches = {
        push_index: PatchInfo.from_files(push_files)
        for push_index, push_files in files.items()
    }
    logger.info("Patches found for %d pushes", len(patches))
    return regressions, known_configs, patches


def split_push_index(
    regressions: Sequence[test_scheduling.ConfigSelectRegression],
) -> int:
    """The first push of the test set: the last 10% of the pushes with regressions."""
    pushes = sorted({r.push_index for r in regressions})
    return pushes[math.floor(TRAIN_PUSH_FRACTION * len(pushes))]


class ConfigSelector:
    """Risk-weighted selection of the configs of the groups of a push, from a model's probabilities.

    Subclasses implement `_group_state` (what they need to compute a group's probabilities) and
    `_probabilities` (P(config catches the group's regression | the selected configs passed)).
    """

    def _group_state(
        self, group: str, candidates: Sequence[str], patch: PatchInfo
    ) -> Any:
        raise NotImplementedError

    def _probabilities(
        self,
        items: Sequence[tuple[PushCandidates, GroupCandidates, str, Sequence[str]]],
    ) -> np.ndarray:
        """For each (push, group, config, selected configs), P(config catches the regression | selected passed)."""
        raise NotImplementedError

    def _select(
        self, pushes: Sequence[PushCandidates], threshold: float
    ) -> list[list[list[str]]]:
        """Select configs for the groups of each push (see select_configs)."""
        selected: list[list[list[str]]] = [[[] for _ in push.groups] for push in pushes]
        coverage = [[0.0 for _ in push.groups] for push in pushes]
        used: list[set[str]] = [set() for _ in pushes]
        active = {
            (p, g) for p, push in enumerate(pushes) for g in range(len(push.groups))
        }

        def cost(p: int, config: str) -> float:
            setup = 0 if config in used[p] else SETUP_COST_MULTIPLIER
            return config_cost(config) * (1 + setup)

        for _ in range(MAX_STEPS):
            items = [
                (p, g, config)
                for p, g in sorted(active)
                for config in pushes[p].groups[g].candidates
                if config not in selected[p][g]
            ]
            if not items:
                break

            probabilities = self._probabilities(
                [
                    (pushes[p], pushes[p].groups[g], config, selected[p][g])
                    for p, g, config in items
                ]
            )
            by_group: dict[tuple[int, int], dict[str, float]] = collections.defaultdict(
                dict
            )
            for (p, g, config), probability in zip(items, probabilities):
                by_group[(p, g)][config] = float(probability)

            def ratio(p: int, g: int, config: str, by_group=by_group) -> float:
                gain = (1 - coverage[p][g]) * by_group[(p, g)][config]
                return pushes[p].groups[g].risk * gain / cost(p, config)

            # Visit the groups by decreasing best ratio, so configs useful to several groups are
            # added first and then reused (without their setup cost) by the others.
            order = sorted(
                by_group,
                key=lambda pg: -max(ratio(pg[0], pg[1], c) for c in by_group[pg]),
            )
            active = set()
            for p, g in order:
                config = max(by_group[(p, g)], key=lambda c: ratio(p, g, c))
                # Every group gets at least one config.
                if ratio(p, g, config) >= threshold or not selected[p][g]:
                    selected[p][g].append(config)
                    coverage[p][g] += (1 - coverage[p][g]) * by_group[(p, g)][config]
                    used[p].add(config)
                    active.add((p, g))

        # Very likely regressions run on every required OS family: add the cheapest (preferring
        # configs already used by the push) of each family that isn't represented yet.
        for p, push in enumerate(pushes):
            for g, group in enumerate(push.groups):
                covered = {
                    test_scheduling.config_platform_family(c) for c in selected[p][g]
                }
                for family in sorted(group.required_families - covered):
                    configs = [
                        c
                        for c in group.candidates
                        if test_scheduling.config_platform_family(c) == family
                    ]
                    if configs:
                        config = min(configs, key=lambda c: (cost(p, c), len(c), c))
                        selected[p][g].append(config)
                        used[p].add(config)

        return selected

    def select_configs(
        self,
        group_risks: Mapping[str, float],
        threshold: float,
        candidates_by_group: Mapping[str, Sequence[str]],
        patch_files: Collection[str] = (),
        allowed_families: Collection[str] | None = None,
        very_likely_groups: Collection[str] = (),
    ) -> dict[str, list[str]]:
        """Select the configs to run the groups of a push on.

        `group_risks` maps each group to its probability of regressing (e.g. from the group selection
        model). Candidates are the configs in `candidates_by_group` (see live_candidates). A config
        is added to a group while the risk times the gain in the probability of catching the group's
        regression, divided by the config's cost, is at least `threshold`. `very_likely_groups` (e.g.
        the manifests including the modified tests) also get a config of each OS family.

        `patch_files` are the files modified by the push. If `allowed_families` is given (the OS
        families building the modified files, see build_platforms), configs of other families are
        not candidates, unless a group has no candidate of the allowed families.

        Groups without candidates are not in the result (callers should not restrict them).
        """
        patch = PatchInfo.from_files(patch_files) if patch_files else NO_PATCH_INFO
        groups = []
        for group, risk in group_risks.items():
            candidates = list(candidates_by_group.get(group, ()))
            if allowed_families is not None:
                allowed = [
                    c
                    for c in candidates
                    if test_scheduling.config_platform_family(c) in allowed_families
                ]
                candidates = allowed or candidates
            if not candidates:
                continue

            required: frozenset[str] = frozenset()
            if group in very_likely_groups:
                required = frozenset(
                    test_scheduling.config_platform_family(c) for c in candidates
                )
            groups.append(
                GroupCandidates(
                    group,
                    candidates,
                    self._group_state(group, candidates, patch),
                    risk,
                    required,
                )
            )

        selected = self._select([PushCandidates(groups, patch)], threshold)[0]
        return {g.group: sorted(configs) for g, configs in zip(groups, selected)}

    def _print_evaluation(
        self,
        pushes: Sequence[
            Sequence[tuple[test_scheduling.ConfigSelectRegression, GroupCandidates]]
        ],
        patches: Sequence[PatchInfo],
    ) -> None:
        """Catch rate and cost on the regressions of the test pushes, at several thresholds.

        Only the configs whose outcome is known are candidates. A regression is caught if a selected
        config failed; "share" is the per-push mean of the regressed groups caught. The cost is the
        per-push cost of the selection (setup cost of each config, plus the cost of each group run).
        """
        logger.info(
            "Evaluating on %d regressions on %d pushes",
            sum(len(p) for p in pushes),
            len(pushes),
        )

        def summarize(name: str, selections: list[list[list[str]]]) -> list[Any]:
            shares: list[float] = []
            caught: list[bool] = []
            costs: list[float] = []
            configs: list[int] = []
            for push, push_selections in zip(pushes, selections):
                hits = [
                    bool(set(s) & regression.failed)
                    for (regression, _), s in zip(push, push_selections)
                ]
                shares.append(float(np.mean(hits)))
                caught.append(any(hits))
                used = set().union(*push_selections)
                costs.append(
                    SETUP_COST_MULTIPLIER * sum(config_cost(c) for c in used)
                    + sum(config_cost(c) for s in push_selections for c in s)
                )
                configs.extend(len(s) for s in push_selections)
            return [
                name,
                np.mean(shares),
                np.mean(caught),
                np.mean(configs),
                np.mean(costs),
            ]

        table = [
            summarize(
                "cheapest config",
                [
                    [[_plainest_cheapest(g.candidates)] for _, g in push]
                    for push in pushes
                ],
            )
        ]
        candidates = [
            PushCandidates([g for _, g in push], patch)
            for push, patch in zip(pushes, patches)
        ]
        for threshold in EVALUATION_THRESHOLDS:
            table.append(
                summarize(f"threshold {threshold}", self._select(candidates, threshold))
            )

        print(
            tabulate(
                table,
                headers=[
                    "selection",
                    "share of regressed groups caught",
                    "pushes caught",
                    "configs per group",
                    "cost per push",
                ],
                floatfmt=".3f",
                tablefmt="github",
            ),
            end="\n\n",
        )


class TestConfigSelectModel(ConfigSelector, Model):
    """P(group fails on config | group regressed, the configs already selected for it passed)."""

    def __init__(self, lemmatization=False):
        Model.__init__(self, lemmatization)

        self.training_dbs = [
            test_scheduling.PUSH_DATA_CONFIG_GROUP_DB,
            repository.COMMITS_DB,
        ]

        self.cross_validation_enabled = False
        self.calculate_importance = False
        self.entire_dataset_training = True

        # Inference state: the failure history up to the end of the training data, and the configs
        # seen recently (to name the tasks of the current task graph, see live_candidates).
        self.history = ConfigHistory()
        self.known_configs: set[str] = set()

        self._regressions: list[test_scheduling.ConfigSelectRegression] | None = None
        self._patches: dict[int, PatchInfo] = {}
        self.row_weights: list[float] = []
        self.row_push_index: list[int] = []
        self.evaluation_regressions: list[
            tuple[test_scheduling.ConfigSelectRegression, dict[str, dict[str, float]]]
        ] = []

        self.extraction_pipeline = Pipeline(
            [
                (
                    "commit_extractor",
                    commit_features.CommitExtractor(
                        [
                            test_scheduling_features.ConfigTokens(),
                            test_scheduling_features.ConfigFailureHistory(),
                            test_scheduling_features.CandidateConfigs(),
                            test_scheduling_features.SelectedConfigs(),
                            test_scheduling_features.PatchPlatformHints(),
                        ],
                        [],
                    ),
                ),
            ]
        )

        self.clf = Pipeline(
            [
                ("union", ColumnTransformer([("data", DictVectorizer(), "data")])),
                (
                    "estimator",
                    xgboost.XGBClassifier(
                        n_estimators=400,
                        max_depth=5,
                        learning_rate=0.05,
                        min_child_weight=10,
                        subsample=0.8,
                        colsample_bytree=0.8,
                        n_jobs=utils.get_physical_cpu_count(),
                    ),
                ),
            ]
        )

    def __getstate__(self):
        # Training data and per-row metadata are only needed while training.
        state = self.__dict__.copy()
        for key in (
            "_regressions",
            "_patches",
            "row_weights",
            "row_push_index",
            "evaluation_regressions",
        ):
            state.pop(key, None)
        return state

    def _load_regressions(self) -> list[test_scheduling.ConfigSelectRegression]:
        if self._regressions is None:
            self._regressions, self.known_configs, self._patches = load_regressions()
        return self._regressions

    def _split_push_index(self) -> int:
        return split_push_index(self._load_regressions())

    def get_labels(self):
        classes = {}
        for _, _, rows in generate_rows(
            self._load_regressions(), ConfigHistory(), self._patches
        ):
            for row in rows:
                classes[row.key] = row.label

        logger.info(
            "%d rows, %d of which failed",
            len(classes),
            sum(label == 1 for label in classes.values()),
        )
        return classes, [0, 1]

    def items_gen(self, classes):
        self.history = ConfigHistory()
        self.row_weights = []
        self.row_push_index = []
        self.evaluation_regressions = []
        split = self._split_push_index()

        for regression, history_features, rows in generate_rows(
            self._load_regressions(), self.history, self._patches
        ):
            if regression.push_index >= split:
                self.evaluation_regressions.append((regression, history_features))

            for row in rows:
                if row.key not in classes:
                    continue

                self.row_weights.append(row.weight)
                self.row_push_index.append(regression.push_index)
                yield {"test_job": row.test_job}, row.label

        # At inference, all the regressions in the training data are known.
        self.history.flush()

    def train_test_split(self, X, y):
        split = self._split_push_index()
        train_len = sum(1 for index in self.row_push_index if index < split)
        logger.info(
            "%d rows in the training set, %d in the test set",
            train_len,
            len(y) - train_len,
        )
        return X[:train_len], X[train_len:], y[:train_len], y[train_len:]

    def get_sample_weights(self, y):
        # The training samples are a prefix of the rows generated by items_gen.
        return np.array(self.row_weights[: len(y)])

    def _predict(self, test_jobs: list[dict[str, Any]]) -> np.ndarray:
        X = self.extraction_pipeline.transform(
            lambda: ({"test_job": test_job} for test_job in test_jobs)
        )
        return self.clf.predict_proba(X)[:, 1]

    def _group_state(
        self, group: str, candidates: Sequence[str], patch: PatchInfo
    ) -> dict[str, dict[str, float]]:
        return {c: self.history.features(group, c) for c in candidates}

    def _probabilities(self, items):
        return self._predict(
            [
                make_test_job(
                    group.group,
                    config,
                    group.candidates,
                    group.state,
                    selected,
                    push.patch,
                )
                for push, group, config, selected in items
            ]
        )

    def evaluation(self) -> None:
        by_push: dict[int, list] = collections.defaultdict(list)
        for regression, history_features in self.evaluation_regressions:
            by_push[regression.push_index].append(
                (
                    regression,
                    GroupCandidates(
                        regression.group,
                        sorted(regression.observed),
                        history_features,
                        1.0,
                    ),
                )
            )
        self._print_evaluation(
            list(by_push.values()),
            [self._patches.get(push_index, NO_PATCH_INFO) for push_index in by_push],
        )

    def get_feature_names(self):
        return self.clf.named_steps["union"].get_feature_names_out()
