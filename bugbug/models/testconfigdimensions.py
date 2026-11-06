# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Config selection from the dimensions of the configs (alternatives to the sequential model of testconfigselect).

A config is described by its dimensions (test_scheduling.config_dimensions): OS family, platform, build type and
variant. A regression shows up on a config iff it shows up on each of the config's dimension values, so configs
sharing a value (e.g. Linux opt and Linux debug) fail together when that value doesn't fail.

- TestConfigSelectPooledModel: each dimension value fails independently, with a probability estimated from counts
  in past regressions, shrunk from the global rate to the suite's, each ancestor directory's and the group's.
- TestConfigSelectValueModel: the same structure, with the probabilities predicted by a binary classifier from the
  counts at each level (and the patch).
- TestConfigSelectClassModel: per dimension, a multiclass classifier predicts whether a regression is independent of
  the dimension ("any") or specific to one of its values ("windows", "debug", ...).

All three use the selection of testconfigselect.ConfigSelector, with P(config catches the regression | the selected
configs passed) computed from sampled value events (first two) or exactly over the class combinations (third).
"""

import collections
import itertools
import logging
import os
import pickle
import zlib
from typing import Any, Iterator, Mapping, Sequence

import numpy as np
import pandas as pd
import xgboost
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction import DictVectorizer
from sklearn.pipeline import Pipeline

from bugbug import (
    commit_features,
    repository,
    test_scheduling,
    test_scheduling_features,
    utils,
)
from bugbug.model import Model
from bugbug.models.testconfigselect import (
    NO_PATCH_INFO,
    POSITIVE_WEIGHT_K,
    ConfigSelector,
    GroupCandidates,
    PatchInfo,
    _suite,
    load_regressions,
    split_push_index,
)

logger = logging.getLogger(__name__)

KINDS = test_scheduling.CONFIG_DIMENSIONS
# Outcomes of the dimension value events sampled per group.
SAMPLES = 1000
# Class probabilities and class combinations below these are ignored.
MIN_CLASS_PROBABILITY = 0.01
MIN_COMBINATION_PROBABILITY = 1e-4

Value = tuple[str, str]  # (kind, value), e.g. ("family", "windows")


def dimension_values(configs: Sequence[str]) -> set[Value]:
    return {
        (kind, value)
        for config in configs
        for kind, value in test_scheduling.config_dimensions(config).items()
    }


def dimension_label(
    regression: test_scheduling.ConfigSelectRegression, kind: str
) -> str | None:
    """'any' if all the observed values of the dimension failed (or several did), the value if only it did.

    None if all the observed configs share the value (nothing to learn).
    """
    observed = {test_scheduling.config_dimensions(c)[kind] for c in regression.observed}
    failed = {test_scheduling.config_dimensions(c)[kind] for c in regression.failed}
    if len(observed) < 2 or not failed:
        return None
    return f"{kind}={failed.pop()}" if len(failed) == 1 else "any"


def history_levels(group: str) -> list[tuple[str, str]]:
    """(level, key) from the most specific to the most general: the group, its directories, its suite, everything."""
    parts = group.split("/")[:-1]
    directories = ["/".join(parts[: i + 1]) for i in range(len(parts))][::-1]
    return (
        [("group", f"g:{group}")]
        + [(f"dir{min(i + 1, 4)}", f"d:{d}") for i, d in enumerate(directories)]
        + [("suite", f"s:{_suite(group)}"), ("global", "*")]
    )


class DimensionHistory:
    """Per-dimension-value failure counts and per-dimension label counts in past regressions, at each level.

    As for testconfigselect.ConfigHistory, a regression is counted CONFIG_SELECT_MAX_LATER pushes after its push.
    """

    def __init__(self, delay: int = test_scheduling.CONFIG_SELECT_MAX_LATER) -> None:
        self.delay = delay
        self.failures: collections.Counter = collections.Counter()
        self.runs: collections.Counter = collections.Counter()
        self.labels: collections.Counter = collections.Counter()
        self.labelled: collections.Counter = collections.Counter()
        self.pending: collections.deque = collections.deque()

    def add(self, regression: test_scheduling.ConfigSelectRegression) -> None:
        self.pending.append(regression)

    def advance(self, push_index: int) -> None:
        while self.pending and self.pending[0].push_index < push_index - self.delay:
            self._count(self.pending.popleft())

    def flush(self) -> None:
        while self.pending:
            self._count(self.pending.popleft())

    def _count(self, regression: test_scheduling.ConfigSelectRegression) -> None:
        observed = dimension_values(sorted(regression.observed))
        failed = dimension_values(sorted(regression.failed))
        labels = {kind: dimension_label(regression, kind) for kind in KINDS}
        for _, key in history_levels(regression.group):
            for value in observed:
                self.runs[key, value] += 1
                self.failures[key, value] += value in failed
            for kind, label in labels.items():
                if label is not None:
                    self.labelled[key, kind] += 1
                    self.labels[key, kind, label] += 1

    def rate(self, group: str, value: Value) -> float:
        """P(value fails | group regressed), shrunk from the global rate to the suite's, the directories' and the group's."""
        rate = 0.5
        for level, key in reversed(history_levels(group)):
            strength = {"global": 2, "suite": 10, "group": 3}.get(level, 5)
            rate = (self.failures[key, value] + strength * rate) / (
                self.runs[key, value] + strength
            )
        return rate

    def value_features(
        self, group: str, value: Value, patch: PatchInfo
    ) -> dict[str, float]:
        kind, name = value
        features: dict[str, float] = {
            f"kind={kind}": 1,
            f"{kind}={name}": 1,
            f"suite={_suite(group)}": 1,
            "pooled_rate": self.rate(group, value),
        }
        for level, key in history_levels(group):
            runs = self.runs[key, value]
            features[f"runs_{level}"] = features.get(f"runs_{level}", 0) + runs
            if runs:
                features[f"failure_rate_{level}"] = self.failures[key, value] / runs
        if kind == "family" and patch.files_num:
            features.update(_patch_family_features(patch, name))
        return features

    def class_features(
        self, group: str, kind: str, values: Sequence[str], patch: PatchInfo
    ) -> dict[str, float]:
        features: dict[str, float] = {
            f"kind={kind}": 1,
            f"suite={_suite(group)}": 1,
            "values": len(values),
        }
        for value in values:
            features[f"available={kind}={value}"] = 1
        for level, key in history_levels(group):
            labelled = self.labelled[key, kind]
            features[f"labelled_{level}"] = (
                features.get(f"labelled_{level}", 0) + labelled
            )
            if labelled:
                features[f"any_{level}"] = self.labels[key, kind, "any"] / labelled
            for value in values:
                if labelled:
                    features[f"only_{level}={kind}={value}"] = (
                        self.labels[key, kind, f"{kind}={value}"] / labelled
                    )
                runs = self.runs[key, (kind, value)]
                if runs:
                    features[f"failure_rate_{level}={kind}={value}"] = (
                        self.failures[key, (kind, value)] / runs
                    )
        if kind == "family" and patch.files_num:
            for family, hints in patch.hints.items():
                features[f"patch_hints={family}"] = hints / patch.files_num
        return features


def _patch_family_features(patch: PatchInfo, family: str) -> dict[str, float]:
    own = patch.hints.get(family, 0)
    other = sum(count for f, count in patch.hints.items() if f != family)
    return {
        "patch_hints_own_family": own / patch.files_num,
        "patch_hints_other_families": other / patch.files_num,
    }


class ValueEvents:
    """Sampled outcomes of a group's dimension value events, independent of each other."""

    def __init__(
        self, group: str, candidates: Sequence[str], rates: Mapping[Value, float]
    ) -> None:
        rng = np.random.default_rng(zlib.crc32(group.encode()))
        events = {
            value: rng.random(SAMPLES) < rate for value, rate in sorted(rates.items())
        }
        self.index = {config: i for i, config in enumerate(candidates)}
        self.fails = np.column_stack(
            [
                np.logical_and.reduce(
                    [
                        events[kind, value]
                        for kind, value in test_scheduling.config_dimensions(
                            config
                        ).items()
                    ]
                )
                for config in candidates
            ]
        )

    def probability(self, config: str, selected: Sequence[str]) -> float:
        possible = (
            ~self.fails[:, [self.index[s] for s in selected]].any(axis=1)
            if selected
            else np.ones(SAMPLES, dtype=bool)
        )
        if not possible.any():
            return 0.0
        return float(self.fails[possible, self.index[config]].mean())


class ClassCombinations:
    """The combinations of one class per dimension, with their probabilities, and which candidates fail in each."""

    def __init__(
        self, candidates: Sequence[str], classes: Mapping[str, Mapping[str, float]]
    ) -> None:
        combinations = []
        for combination in itertools.product(
            *(classes[kind].items() for kind in KINDS)
        ):
            probability = float(np.prod([p for _, p in combination]))
            if probability >= MIN_COMBINATION_PROBABILITY:
                combinations.append((probability, [label for label, _ in combination]))
        self.index = {config: i for i, config in enumerate(candidates)}
        self.probabilities = np.array([p for p, _ in combinations])
        self.probabilities /= self.probabilities.sum()
        self.fails = np.array(
            [
                [
                    all(
                        label == "any" or label == f"{kind}={dims[kind]}"
                        for kind, label in zip(KINDS, labels)
                    )
                    for dims in (
                        test_scheduling.config_dimensions(c) for c in candidates
                    )
                ]
                for _, labels in combinations
            ]
        )

    def probability(self, config: str, selected: Sequence[str]) -> float:
        possible = (
            ~self.fails[:, [self.index[s] for s in selected]].any(axis=1)
            if selected
            else np.ones(len(self.probabilities), dtype=bool)
        )
        mass = self.probabilities[possible].sum()
        if mass == 0:
            return 0.0
        return float(
            self.probabilities[possible][self.fails[possible, self.index[config]]].sum()
            / mass
        )


class DimensionSelector(ConfigSelector):
    def _probabilities(self, items):
        return np.array(
            [
                group.state.probability(config, selected)
                for _, group, config, selected in items
            ]
        )

    def _evaluate(
        self,
        regressions: Sequence[tuple[test_scheduling.ConfigSelectRegression, Any]],
        patches: Mapping[int, PatchInfo],
    ) -> None:
        """Print the evaluation table for the given test regressions and their probability states."""
        by_push: dict[int, list] = collections.defaultdict(list)
        for regression, state in regressions:
            by_push[regression.push_index].append(
                (
                    regression,
                    GroupCandidates(
                        regression.group, sorted(regression.observed), state, 1.0
                    ),
                )
            )
        self._print_evaluation(
            list(by_push.values()),
            [patches.get(push_index, NO_PATCH_INFO) for push_index in by_push],
        )


def _save(model: Model) -> None:
    """Save a model without classifier, as Model.train does."""
    model_directory = model.__class__.__name__.lower()
    os.makedirs(model_directory, exist_ok=True)
    with open(os.path.join(model_directory, "model.pkl"), "wb") as f:
        pickle.dump(model, f, protocol=pickle.HIGHEST_PROTOCOL)


class TestConfigSelectPooledModel(DimensionSelector, Model):
    """P(dimension value fails | group regressed) from shrunk counts, no classifier."""

    def __init__(self, lemmatization=False):
        Model.__init__(self, lemmatization)
        self.training_dbs = [test_scheduling.PUSH_DATA_CONFIG_GROUP_DB]
        self.history = DimensionHistory()
        self.known_configs: set[str] = set()

    def train(self, importance_cutoff=0.15, limit=None):
        regressions, self.known_configs, _ = load_regressions(with_patches=False)
        split = split_push_index(regressions)

        self.history = DimensionHistory()
        test = []
        for regression in regressions:
            self.history.advance(regression.push_index)
            if regression.push_index >= split:
                candidates = sorted(regression.observed)
                test.append(
                    (
                        regression,
                        self._group_state(regression.group, candidates, NO_PATCH_INFO),
                    )
                )
            self.history.add(regression)
        self._evaluate(test, {})

        # At inference, all the regressions are known.
        self.history.flush()
        _save(self)
        return {}

    def _group_state(self, group, candidates, patch):
        return ValueEvents(
            group,
            candidates,
            {
                value: self.history.rate(group, value)
                for value in dimension_values(candidates)
            },
        )


class _DimensionClassifierModel(DimensionSelector, Model):
    """Shared training of the two classifier models: time-ordered rows from the regressions."""

    def __init__(self, lemmatization=False):
        Model.__init__(self, lemmatization)
        self.training_dbs = [
            test_scheduling.PUSH_DATA_CONFIG_GROUP_DB,
            repository.COMMITS_DB,
        ]
        self.cross_validation_enabled = False
        self.calculate_importance = False
        self.entire_dataset_training = True

        self.history = DimensionHistory()
        self.known_configs: set[str] = set()
        self._data: tuple | None = None
        self.row_weights: list[float] = []
        self.row_push_index: list[int] = []
        self.evaluation_regressions: list = []

        self.extraction_pipeline = Pipeline(
            [
                (
                    "commit_extractor",
                    commit_features.CommitExtractor(
                        [test_scheduling_features.ConfigDimensionFeatures()], []
                    ),
                )
            ]
        )
        self.clf: Pipeline = _xgboost()

    def __getstate__(self):
        state = self.__dict__.copy()
        for key in (
            "_data",
            "row_weights",
            "row_push_index",
            "evaluation_regressions",
            "_placeholders",
        ):
            state.pop(key, None)
        return state

    def _load(self):
        if self._data is None:
            regressions, self.known_configs, patches = load_regressions()
            self._data = (regressions, patches, split_push_index(regressions))
        return self._data

    def _rows(
        self,
        regression: test_scheduling.ConfigSelectRegression,
        history: DimensionHistory,
        patch: PatchInfo,
    ) -> list[tuple[tuple, dict[str, float], Any]]:
        """(key, features, label) of a regression's training rows."""
        raise NotImplementedError

    def _generate(
        self, history: DimensionHistory
    ) -> Iterator[tuple[test_scheduling.ConfigSelectRegression, list]]:
        regressions, patches, _ = self._load()
        for regression in regressions:
            history.advance(regression.push_index)
            yield (
                regression,
                self._rows(
                    regression,
                    history,
                    patches.get(regression.push_index, NO_PATCH_INFO),
                ),
            )
            history.add(regression)

    def get_labels(self):
        classes = {
            key: label
            for _, rows in self._generate(DimensionHistory())
            for key, _, label in rows
        }
        logger.info("%d rows", len(classes))
        return classes, sorted(set(classes.values()))

    def items_gen(self, classes):
        regressions, patches, split = self._load()
        groups_per_push = collections.Counter(r.push_index for r in regressions)
        self.history = DimensionHistory()
        self.row_weights, self.row_push_index, self.evaluation_regressions = [], [], []
        for regression, rows in self._generate(self.history):
            if regression.push_index >= split:
                # The test regressions' probability states need the classifier: computed in `evaluation`.
                patch = patches.get(regression.push_index, NO_PATCH_INFO)
                self.evaluation_regressions.append(
                    (
                        regression,
                        self._state_inputs(
                            regression.group, sorted(regression.observed), patch
                        ),
                    )
                )
            weight = POSITIVE_WEIGHT_K / max(
                POSITIVE_WEIGHT_K, groups_per_push[regression.push_index]
            )
            for key, features, label in rows:
                if key in classes:
                    self.row_weights.append(weight)
                    self.row_push_index.append(regression.push_index)
                    yield {"test_job": {"features": features}}, label
        self.history.flush()

    def train_test_split(self, X, y):
        split = self._load()[2]
        train_len = sum(1 for index in self.row_push_index if index < split)
        return X[:train_len], X[train_len:], y[:train_len], y[train_len:]

    def get_sample_weights(self, y):
        return np.array(self.row_weights[: len(y)])

    def _predict_proba(self, features: Sequence[dict[str, float]]) -> np.ndarray:
        X = self.extraction_pipeline.transform(
            lambda: ({"test_job": {"features": f}} for f in features)
        )
        return self.clf.predict_proba(X)

    def _state_inputs(
        self, group: str, candidates: Sequence[str], patch: PatchInfo
    ) -> Any:
        """The features the probability state of a group is computed from (with the current history)."""
        raise NotImplementedError

    def _state(self, group: str, candidates: Sequence[str], inputs: Any) -> Any:
        raise NotImplementedError

    def _group_state(self, group, candidates, patch):
        return self._state(
            group, candidates, self._state_inputs(group, candidates, patch)
        )

    def evaluation(self) -> None:
        _, patches, _ = self._load()
        self._evaluate(
            [
                (
                    regression,
                    self._state(regression.group, sorted(regression.observed), inputs),
                )
                for regression, inputs in self.evaluation_regressions
            ],
            patches,
        )

    def get_feature_names(self):
        return self.clf.named_steps["union"].get_feature_names_out()


def _xgboost(**kwargs) -> Pipeline:
    return Pipeline(
        [
            ("union", ColumnTransformer([("data", DictVectorizer(), "data")])),
            (
                "estimator",
                xgboost.XGBClassifier(
                    n_estimators=300,
                    max_depth=4,
                    learning_rate=0.05,
                    subsample=0.8,
                    colsample_bytree=0.8,
                    n_jobs=utils.get_physical_cpu_count(),
                    **kwargs,
                ),
            ),
        ]
    )


class TestConfigSelectValueModel(_DimensionClassifierModel):
    """P(dimension value fails | group regressed) from a binary classifier, with independent value events."""

    def __init__(self, lemmatization=False):
        super().__init__(lemmatization)
        self.clf = _xgboost(min_child_weight=10)

    def _rows(self, regression, history, patch):
        failed = dimension_values(sorted(regression.failed))
        return [
            (
                (regression.revs[0], regression.group, value),
                history.value_features(regression.group, value, patch),
                int(value in failed),
            )
            for value in sorted(dimension_values(sorted(regression.observed)))
        ]

    def _state_inputs(self, group, candidates, patch):
        values = sorted(dimension_values(candidates))
        return values, [
            self.history.value_features(group, value, patch) for value in values
        ]

    def _state(self, group, candidates, inputs):
        values, features = inputs
        rates = self._predict_proba(features)[:, 1]
        return ValueEvents(group, candidates, dict(zip(values, rates)))


class TestConfigSelectClassModel(_DimensionClassifierModel):
    """Per dimension, P(regression independent of the dimension ("any") or specific to one of its values)."""

    def __init__(self, lemmatization=False):
        super().__init__(lemmatization)
        self.clf = _xgboost(min_child_weight=5, objective="multi:softprob")

    def fit_classifier(self, X, y):
        # XGBoost needs every class in the training data, but a class can be only in the test set (e.g. a
        # platform that appeared recently): add a placeholder row for each missing class, with no weight.
        missing = sorted(set(range(len(self.le.classes_))) - set(y))
        self._placeholders = len(missing)
        if missing:
            X = pd.concat([X, X.iloc[[0] * len(missing)]], ignore_index=True)
            y = np.concatenate([y, missing])
        super().fit_classifier(X, y)

    def get_sample_weights(self, y):
        placeholders = getattr(self, "_placeholders", 0)
        weights = np.array(self.row_weights[: len(y) - placeholders])
        return np.concatenate([weights, np.full(placeholders, 1e-6)])

    def _rows(self, regression, history, patch):
        rows = []
        for kind in KINDS:
            label = dimension_label(regression, kind)
            if label is not None:
                values = sorted(
                    {
                        test_scheduling.config_dimensions(c)[kind]
                        for c in regression.observed
                    }
                )
                rows.append(
                    (
                        (regression.revs[0], regression.group, kind),
                        history.class_features(regression.group, kind, values, patch),
                        label,
                    )
                )
        return rows

    def _state_inputs(self, group, candidates, patch):
        inputs = {}
        for kind in KINDS:
            values = sorted(
                {test_scheduling.config_dimensions(c)[kind] for c in candidates}
            )
            inputs[kind] = (
                values,
                self.history.class_features(group, kind, values, patch)
                if len(values) > 1
                else None,
            )
        return inputs

    def _state(self, group, candidates, inputs):
        to_predict = [kind for kind in KINDS if inputs[kind][1] is not None]
        probabilities = (
            self._predict_proba([inputs[kind][1] for kind in to_predict])
            if to_predict
            else []
        )
        classes = {kind: {"any": 1.0} for kind in KINDS}
        for kind, p in zip(to_predict, probabilities):
            values = inputs[kind][0]
            allowed = {"any"} | {f"{kind}={value}" for value in values}
            by_class = {
                label: float(probability)
                for label, probability in zip(self.le.classes_, p)
                if label in allowed
            }
            total = sum(by_class.values())
            if total > 0:
                kept = {
                    c: q / total
                    for c, q in by_class.items()
                    if q / total >= MIN_CLASS_PROBABILITY
                }
                total = sum(kept.values())
                classes[kind] = {c: q / total for c, q in kept.items()} or {"any": 1.0}
        return ClassCombinations(candidates, classes)
