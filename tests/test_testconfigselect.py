# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from typing import Any

import numpy as np
import pytest

from bugbug.models import testconfigselect
from bugbug.models.testconfigselect import (
    ConfigHistory,
    GroupCandidates,
    PatchInfo,
    PushCandidates,
)
from bugbug.test_scheduling import ConfigSelectRegression, Group, Revision

LINUX = "test-linux2404-64/opt-*"
LINUX_DEBUG = "test-linux2404-64/debug-*"
WINDOWS = "test-windows11-64-25h2/opt-*"
MAC = "test-macosx1500-aarch64/opt-*"
G = Group("dom/base/test/mochitest.toml")
G2 = Group("dom/base/test/browser.toml")


def regression(push_index: int, failed: set[str], observed: set[str], group=G):
    return ConfigSelectRegression(
        push_index,
        (Revision(f"rev{push_index}"),),
        group,
        frozenset(failed),
        frozenset(observed | failed),
    )


def test_config_history_is_delayed() -> None:
    history = ConfigHistory(delay=10)
    history.add(regression(0, {WINDOWS}, {LINUX}))

    # The regression's later passes may still be unknown 10 pushes later.
    history.advance(10)
    assert history.features(G, WINDOWS)["group_regressions"] == 0
    assert "failure_rate_group_config" not in history.features(G, WINDOWS)

    history.advance(11)
    features = history.features(G, WINDOWS)
    assert features["group_regressions"] == 1
    assert features["runs_group_config"] == 1
    assert features["failure_rate_group_config"] == 1.0
    assert features["group_all_failed"] == 0.0
    assert features["group_failed_fraction"] == 0.5
    # A config sharing tokens with the failing one (windows11, 25h2, opt) gets its token rates.
    other = history.features(G, "test-windows11-64-25h2/debug-*")
    assert "failure_rate_group_config" not in other
    assert other["failure_rate_group_token_max"] == 1.0


def test_config_history_new_platform_version() -> None:
    # A new version of a platform shares the history of the previous one through its family token.
    history = ConfigHistory(delay=0)
    for i in range(5):
        history.add(regression(i, {"test-macosx1015-64-qr/opt-*"}, {LINUX, WINDOWS}))
    history.advance(100)
    new_mac = history.features(G, "test-macosx1500-aarch64/opt-*")
    assert "failure_rate_group_config" not in new_mac
    assert new_mac["failure_rate_group_token_max"] == 1.0
    assert history.likelihood(G, "test-macosx1500-aarch64/opt-*") > history.likelihood(
        G, LINUX
    )


def test_config_history_likelihood() -> None:
    history = ConfigHistory(delay=0)
    before = history.likelihood(G, WINDOWS)
    for i in range(5):
        history.add(regression(i, {WINDOWS}, {LINUX}))
    history.advance(100)
    assert history.likelihood(G, WINDOWS) > before
    assert history.likelihood(G, WINDOWS) > history.likelihood(G, LINUX)
    assert 0 < history.likelihood(G, LINUX) < 1


def test_generate_rows() -> None:
    regressions = [
        regression(0, {WINDOWS}, {LINUX, MAC}),
        regression(0, {LINUX}, {WINDOWS}, group=G2),
        regression(200, {MAC}, {LINUX, WINDOWS}),
    ]
    patches = {0: PatchInfo({"windows": 1}, 2)}
    generated = list(
        testconfigselect.generate_rows(regressions, ConfigHistory(), patches)
    )
    assert [r for r, _, _ in generated] == regressions

    first, history_features, rows = generated[0]
    assert set(history_features) == {LINUX, MAC, WINDOWS}
    # Two regressions on the push: no down-weighting below POSITIVE_WEIGHT_K groups.
    assert {row.weight for row in rows} == {1 / testconfigselect.ORDERINGS}
    for row in rows:
        assert row.label == int(row.test_job["config"] == WINDOWS)
        # Configs already selected passed, and are not candidates again.
        assert WINDOWS not in row.test_job["selected"]
        assert row.test_job["config"] not in row.test_job["selected"]
        assert row.test_job["patch_hints"] == {"windows": 1}
        assert row.test_job["patch_files_num"] == 2
    # The cheapest-first ordering walks LINUX (cost 2) before the others.
    cheapest_first = [row for row in rows if row.key[2] == 0]
    assert {row.test_job["config"] for row in cheapest_first if row.key[3] == 0} == {
        LINUX,
        MAC,
        WINDOWS,
    }
    assert all(
        row.test_job["selected"] == [LINUX] for row in cheapest_first if row.key[3] == 1
    )
    # Without a known patch.
    assert generated[2][2][0].test_job["patch_files_num"] == 0

    # The first regressions are only counted in the history of pushes more than
    # CONFIG_SELECT_MAX_LATER pushes later.
    assert history_features[WINDOWS]["group_regressions"] == 0
    assert generated[2][1][LINUX]["group_regressions"] == 1

    # Rows are the same at every generation (get_labels and items_gen must agree).
    again = list(testconfigselect.generate_rows(regressions, ConfigHistory(), patches))
    assert [row.key for _, _, r in again for row in r] == [
        row.key for _, _, r in generated for row in r
    ]


def test_live_candidates() -> None:
    known_configs = {
        "test-linux2404-64/opt-*",
        "test-linux2404-64/opt-*-swr",
        "test-macosx1015-64-qr/opt-*",
    }
    manifests_by_task: dict[str, list[str]] = {
        "test-linux2404-64/opt-mochitest-browser-chrome-1": [G2],
        "test-linux2404-64/opt-mochitest-browser-chrome-swr-2": [G2],
        # A platform not in the training data yet: named after its label.
        "test-macosx1500-aarch64/opt-mochitest-browser-chrome-3": [G2],
        "test-linux2404-64/opt-mochitest-plain-4": [f"{G}:other.toml"],
        "build-linux64/opt": [],
    }
    assert testconfigselect.live_candidates(manifests_by_task, known_configs) == {
        # The most specific known pattern names each task; the old macOS config isn't a candidate
        # as no current task matches it.
        G2: [
            "test-linux2404-64/opt-*",
            "test-linux2404-64/opt-*-swr",
            "test-macosx1500-aarch64/opt-mochitest-browser-chrome",
        ],
        G: ["test-linux2404-64/opt-*"],
    }


def synthetic_regressions() -> list[ConfigSelectRegression]:
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
def trained_model(
    monkeypatch: pytest.MonkeyPatch,
) -> testconfigselect.TestConfigSelectModel:
    regressions = synthetic_regressions()
    monkeypatch.setattr(
        testconfigselect.test_scheduling,
        "get_config_select_regressions",
        lambda push_data_factory: (regressions, {LINUX, LINUX_DEBUG, WINDOWS, MAC}),
    )
    monkeypatch.setattr(
        testconfigselect.repository,
        "get_commits",
        lambda **kwargs: iter(
            [{"node": "rev0", "files": ["widget/windows/nsWindow.cpp"]}]
        ),
    )
    model = testconfigselect.TestConfigSelectModel()
    classes, class_names = model.get_labels()
    assert class_names == [0, 1]
    assert model._patches == {0: PatchInfo({"windows": 1}, 1)}
    items = list(model.items_gen(classes))
    assert len(items) == len(classes)
    X = model.extraction_pipeline.transform(lambda: [item for item, _ in items])
    y = np.array([label for _, label in items])

    X_train, X_test, y_train, y_test = model.train_test_split(X, y)
    # The last 10% of the pushes are the test set.
    assert len(X_train) == sum(1 for i in model.row_push_index if i < 540)
    assert len(X_train) + len(X_test) == len(y)
    assert {r.push_index for r, _ in model.evaluation_regressions} == set(
        range(540, 600, 10)
    )
    assert len(model.get_sample_weights(y_train)) == len(y_train)

    model.le.fit(y)
    model.fit_classifier(X, model.le.transform(y))
    return model


def test_model_features_and_history(trained_model) -> None:
    data = trained_model.extraction_pipeline.transform(
        lambda: [
            {
                "test_job": testconfigselect.make_test_job(
                    G,
                    WINDOWS,
                    [LINUX, WINDOWS],
                    {c: trained_model.history.features(G, c) for c in (LINUX, WINDOWS)},
                    [LINUX],
                    PatchInfo({"windows": 1}, 2),
                )
            }
        ]
    )["data"][0]
    assert data["windows11 in ConfigTokens"] is True
    assert data["windows in ConfigTokens"] is True
    assert data["selected"] == 1
    assert data["candidates_same_platform"] == 1
    assert data["patch_hints_own_family"] == 0.5
    # At inference, all the training regressions are in the history.
    assert data["group_regressions"] == 60
    assert any(
        name.startswith("data__failure_rate_group_config")
        for name in trained_model.get_feature_names()
    )


def test_select_configs(trained_model) -> None:
    # A risky group gets more configs than a group that is unlikely to regress.
    selected = trained_model.select_configs({G: 0.9, G2: 0.9}, 0.0005, CANDIDATES)
    assert set(selected) == {G, G2}
    assert MAC in selected[G2]
    assert WINDOWS in selected[G]

    few = trained_model.select_configs({G: 0.001, G2: 0.001}, 0.0005, CANDIDATES)
    assert all(len(configs) == 1 for configs in few.values())

    # Groups without candidates are left out (callers don't restrict them).
    assert trained_model.select_configs({Group("unknown.toml"): 0.9}, 0.0005, {}) == {}

    # Only the given candidates are selected.
    only_linux = trained_model.select_configs(
        {G: 0.9}, 0.0005, {G: [LINUX, LINUX_DEBUG]}
    )
    assert set(only_linux[G]) <= {LINUX, LINUX_DEBUG}


def test_select_configs_very_likely_runs_on_every_family(trained_model) -> None:
    # A high threshold: one config per group, except for very likely regressions, whatever their risk.
    selected = trained_model.select_configs(
        {G: 0.5, G2: 0.01}, 1.0, CANDIDATES, very_likely_groups={G2}
    )
    assert len(selected[G]) == 1
    assert {
        testconfigselect.test_scheduling.config_platform_family(c) for c in selected[G2]
    } == {"linux", "windows", "macosx"}


def test_select_configs_allowed_families(trained_model) -> None:
    # A patch only building on Windows: only Windows configs, even for very likely regressions.
    selected = trained_model.select_configs(
        {G: 1.0, G2: 0.9},
        0.0005,
        CANDIDATES,
        ["widget/windows/a.cpp"],
        {"windows"},
        very_likely_groups={G},
    )
    assert selected == {G: [WINDOWS], G2: [WINDOWS]}

    # A group without candidates of the allowed families keeps its candidates.
    selected = trained_model.select_configs(
        {G2: 0.9}, 0.0005, {G2: [LINUX, MAC]}, (), {"windows"}
    )
    assert selected[G2]
    assert set(selected[G2]) <= {LINUX, MAC}


def test_select_reuses_configs_of_the_push(monkeypatch: pytest.MonkeyPatch) -> None:
    model = testconfigselect.TestConfigSelectModel()

    # Both groups fail on WINDOWS (more expensive) a bit more often than on MAC (even more
    # expensive), and on LINUX less: once WINDOWS is used by a group, its setup cost is paid.
    probabilities = {LINUX: 0.1, WINDOWS: 0.5, MAC: 0.45}

    def predict(test_jobs: list[dict[str, Any]]) -> np.ndarray:
        return np.array(
            [
                0.0 if test_job["selected"] else probabilities[test_job["config"]]
                for test_job in test_jobs
            ]
        )

    monkeypatch.setattr(model, "_predict", predict)
    history_features = {c: {"likelihood": 0.0} for c in probabilities}
    groups = [
        GroupCandidates(G, [LINUX, WINDOWS, MAC], history_features, 1.0),
        GroupCandidates(G2, [LINUX, WINDOWS, MAC], history_features, 1.0),
    ]
    ((first, second),) = model._select([PushCandidates(groups)], threshold=1.0)
    # Every group gets at least one config, and the second one reuses the first one's.
    assert first == second
    assert len(first) == 1


def test_evaluation_runs(trained_model, capsys) -> None:
    trained_model.evaluation()
    output = capsys.readouterr().out
    assert "cheapest config" in output
    assert "threshold 0.001" in output
