# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import os

import numpy as np
import pytest

from bugbug.models import testconfigdimensions
from bugbug.models.testconfigdimensions import (
    ClassCombinations,
    DimensionHistory,
    ValueEvents,
    dimension_label,
    history_levels,
)
from bugbug.test_scheduling import ConfigSelectRegression, Group, Revision

LINUX = "test-linux2404-64/opt-*"
LINUX_DEBUG = "test-linux2404-64/debug-*"
WINDOWS = "test-windows11-64-25h2/opt-*"
WINDOWS_DEBUG = "test-windows11-64-25h2/debug-*"
MAC = "test-macosx1500-aarch64/opt-*"
G = Group("dom/base/test/mochitest.toml")
G2 = Group("dom/base/test/browser.toml")
OTHER_DIR = Group("layout/style/test/mochitest.toml")


def regression(push_index, failed, observed, group=G):
    return ConfigSelectRegression(
        push_index,
        (Revision(f"rev{push_index}"),),
        group,
        frozenset(failed),
        frozenset(set(observed) | set(failed)),
    )


def test_dimension_label() -> None:
    # Only Windows failed while Linux passed; only debug failed while opt passed.
    r = regression(0, {WINDOWS_DEBUG}, {WINDOWS, LINUX_DEBUG})
    assert dimension_label(r, "family") == "family=windows"
    assert dimension_label(r, "build") == "build=debug"
    # Every observed variant failed.
    assert dimension_label(r, "variant") is None  # a single variant observed
    assert (
        dimension_label(regression(0, {LINUX, WINDOWS}, {MAC, LINUX_DEBUG}), "family")
        == "any"
    )


def test_history_levels() -> None:
    assert history_levels(G) == [
        ("group", "g:dom/base/test/mochitest.toml"),
        ("dir1", "d:dom/base/test"),
        ("dir2", "d:dom/base"),
        ("dir3", "d:dom"),
        ("suite", "s:mochitest"),
        ("global", "*"),
    ]


def test_dimension_history_delay_and_shrinkage() -> None:
    history = DimensionHistory(delay=10)
    for i in range(5):
        history.add(regression(i, {WINDOWS}, {LINUX}))
    history.advance(10)
    assert history.runs["g:" + G, ("family", "windows")] == 0
    history.advance(100)
    assert history.runs["g:" + G, ("family", "windows")] == 5
    windows = ("family", "windows")
    # A group of the same directory without history of its own gets the directory's rate, higher than for a group
    # of another directory.
    assert history.rate(G2, windows) > history.rate(OTHER_DIR, windows)
    assert history.rate(G, windows) > history.rate(G2, windows) > 0.5
    assert history.rate(G, ("family", "linux")) < 0.5
    features = history.value_features(G2, windows, testconfigdimensions.NO_PATCH_INFO)
    assert features["failure_rate_dir1"] == 1.0
    assert "failure_rate_group" not in features
    class_features = history.class_features(
        G2, "family", ["linux", "windows"], testconfigdimensions.NO_PATCH_INFO
    )
    assert class_features["only_dir1=family=windows"] == 1.0


def test_value_events() -> None:
    events = ValueEvents(
        G,
        [LINUX, LINUX_DEBUG, WINDOWS],
        {
            ("family", "linux"): 0.5,
            ("family", "windows"): 0.5,
            ("platform", "linux2404-64"): 1.0,
            ("platform", "windows11-64-25h2"): 1.0,
            ("build", "opt"): 1.0,
            ("build", "debug"): 1.0,
            ("variant", "plain"): 1.0,
        },
    )
    assert events.probability(LINUX, []) == pytest.approx(0.5, abs=0.06)
    # Linux opt and Linux debug share the Linux event: once Linux opt passed, Linux debug can't fail.
    assert events.probability(LINUX_DEBUG, [LINUX]) == 0.0
    assert events.probability(WINDOWS, [LINUX]) == pytest.approx(0.5, abs=0.06)


def test_class_combinations() -> None:
    combinations = ClassCombinations(
        [LINUX, LINUX_DEBUG, WINDOWS],
        {
            "family": {"any": 0.4, "family=windows": 0.6},
            "platform": {"any": 1.0},
            "build": {"any": 0.5, "build=debug": 0.5},
            "variant": {"any": 1.0},
        },
    )
    assert combinations.probability(WINDOWS, []) == pytest.approx(
        0.5
    )  # any build (0.5)
    assert combinations.probability(LINUX_DEBUG, []) == pytest.approx(0.4)  # any family
    # Once Linux opt passed, the family isn't "any" with any build: Linux debug fails if the build is debug.
    assert combinations.probability(LINUX_DEBUG, [LINUX]) == pytest.approx(
        0.4 * 0.5 / (1 - 0.4 * 0.5)
    )


def synthetic_regressions():
    # G fails on Windows two times out of three, on Linux otherwise; G2 always on Mac.
    regressions = []
    for i in range(60):
        regressions.append(
            regression(
                i * 10,
                {WINDOWS if i % 3 else LINUX},
                {LINUX, LINUX_DEBUG, WINDOWS, MAC},
            )
        )
        regressions.append(regression(i * 10, {MAC}, {LINUX, WINDOWS}, group=G2))
    return regressions


CANDIDATES = {G: [LINUX, LINUX_DEBUG, WINDOWS, MAC], G2: [LINUX, WINDOWS, MAC]}


@pytest.fixture
def mock_data(monkeypatch: pytest.MonkeyPatch) -> None:
    regressions = synthetic_regressions()
    monkeypatch.setattr(
        testconfigdimensions,
        "load_regressions",
        lambda with_patches=True: (regressions, {LINUX, LINUX_DEBUG, WINDOWS, MAC}, {}),
    )


def check_selection(model) -> None:
    selected = model.select_configs({G: 0.9, G2: 0.9}, 0.001, CANDIDATES)
    assert MAC in selected[G2]
    assert WINDOWS in selected[G]
    # Few configs for groups unlikely to regress.
    few = model.select_configs({G: 0.001, G2: 0.001}, 0.001, CANDIDATES)
    assert all(len(configs) == 1 for configs in few.values())
    assert model.select_configs({Group("unknown.toml"): 0.9}, 0.001, {}) == {}


def test_pooled_model(mock_data, tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    model = testconfigdimensions.TestConfigSelectPooledModel()
    assert model.train() == {}
    assert "threshold 0.001" in capsys.readouterr().out
    assert os.path.exists("testconfigselectpooledmodel/model.pkl")
    loaded = testconfigdimensions.TestConfigSelectPooledModel.load(
        "testconfigselectpooledmodel"
    )
    check_selection(loaded)


@pytest.mark.parametrize(
    "model_class",
    [
        testconfigdimensions.TestConfigSelectValueModel,
        testconfigdimensions.TestConfigSelectClassModel,
    ],
)
def test_classifier_models(mock_data, model_class, capsys) -> None:
    model = model_class()
    classes, class_names = model.get_labels()
    items = list(model.items_gen(classes))
    assert len(items) == len(classes)
    X = model.extraction_pipeline.transform(lambda: [item for item, _ in items])
    y = np.array([label for _, label in items])
    X_train, X_test, y_train, y_test = model.train_test_split(X, y)
    assert len(X_train) == sum(1 for i in model.row_push_index if i < 540)
    model.le.fit(y)
    model.fit_classifier(X, model.le.transform(y))
    model.evaluation()
    assert "threshold 0.001" in capsys.readouterr().out
    check_selection(model)


def test_class_model_labels(mock_data) -> None:
    model = testconfigdimensions.TestConfigSelectClassModel()
    classes, class_names = model.get_labels()
    assert set(class_names) <= {
        "any",
        "family=linux",
        "family=windows",
        "family=macosx",
        "build=opt",
        "platform=linux2404-64",
        "platform=windows11-64-25h2",
        "platform=macosx1500-aarch64",
    }
    assert "family=windows" in class_names


def test_class_model_class_only_in_test_set(monkeypatch) -> None:
    # Android-specific regressions only in the last pushes (the test set): the classifier still trains.
    android = "test-android-em-14-x86_64/opt-geckoview-*"
    regressions = synthetic_regressions()[:100] + [
        regression(2000 + i * 10, {android}, {LINUX, android}, group=G)
        for i in range(5)
    ]
    monkeypatch.setattr(
        testconfigdimensions,
        "load_regressions",
        lambda with_patches=True: (regressions, {LINUX, WINDOWS, MAC, android}, {}),
    )
    model = testconfigdimensions.TestConfigSelectClassModel()
    classes, class_names = model.get_labels()
    assert "family=android" in class_names
    items = list(model.items_gen(classes))
    X = model.extraction_pipeline.transform(lambda: [item for item, _ in items])
    y = np.array([label for _, label in items])
    X_train, _, y_train, _ = model.train_test_split(X, y)
    assert "family=android" not in set(y_train)
    model.le.fit(y)
    model.fit_classifier(X_train, model.le.transform(y_train))
    assert model.clf.predict_proba(X_train).shape[1] == len(model.le.classes_)
