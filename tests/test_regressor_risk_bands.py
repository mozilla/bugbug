# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from collections import Counter

import numpy as np

from bugbug.models.regressor import RegressorModel, compute_risk_bands


def test_compute_risk_bands():
    risks = np.random.RandomState(0).uniform(size=10000)

    risk_bands = compute_risk_bands(risks)

    assert [name for name, _, _ in risk_bands] == ["l", "a", "h", "v"]
    # The bands are contiguous and cover the whole risk scale.
    assert risk_bands[0][1] == 0.0
    assert risk_bands[-1][2] == 1.0
    for (_, _, end), (_, start, _) in zip(risk_bands, risk_bands[1:]):
        assert end == start

    model = RegressorModel()
    model.risk_bands = risk_bands
    counts = Counter(model.find_risk_band(risk) for risk in risks)
    assert counts == {"l": 5000, "a": 3000, "h": 1500, "v": 500}

    # Boundaries belong to the lower band.
    assert model.find_risk_band(risk_bands[0][2]) == "l"
    assert model.find_risk_band(0.0) == "l"
    assert model.find_risk_band(1.0) == "v"


def test_compute_risk_bands_ties():
    # Isotonic calibration outputs few distinct values, so many risks are tied.
    risks = np.repeat([0.1, 0.2, 0.5, 0.9], [600, 250, 120, 30])

    risk_bands = compute_risk_bands(risks)

    model = RegressorModel()
    model.risk_bands = risk_bands
    counts = Counter(model.find_risk_band(risk) for risk in risks)
    # Each tied group goes to the band whose target share it fits best, and the top
    # band is not left empty even if the 95th percentile falls in a tied group.
    assert counts == {"l": 600, "a": 250, "h": 120, "v": 30}


def test_fit_classifier_bug_weights(monkeypatch):
    import pandas as pd

    model = RegressorModel()
    # Bug 1: 3 commits (regressor), bug 2: 1 commit (regressor), bugs 3-6: clean.
    model.train_bug_ids = [1, 1, 1, 2, 3, 4, 5, 6]
    # As after train_test_split: a subset of rows, shuffled, keeping the original index.
    X = pd.DataFrame({"a": range(8)}, index=[7, 0, 3, 1, 4, 2, 5, 6])
    y = np.array([0, 1, 1, 1, 0, 1, 0, 0])

    captured = {}
    monkeypatch.setattr(
        model.clf,
        "fit",
        lambda X, y, estimator__sample_weight: captured.update(
            w=estimator__sample_weight
        ),
    )
    model.fit_classifier(X, y)

    w = dict(zip(X.index, captured["w"]))
    # Each regressor bug gets the same total weight, split among its commits.
    assert np.isclose(w[0] + w[1] + w[2], w[3])
    assert np.isclose(w[0], w[1]) and np.isclose(w[1], w[2])
    # Both classes have the same total weight.
    assert np.isclose(captured["w"][y == 1].sum(), captured["w"][y == 0].sum())


def test_cross_validation_bug_weights(monkeypatch):
    import pandas as pd
    import pytest

    import bugbug.model

    model = RegressorModel()
    # 40 commits from 20 bugs (2 commits each), the first 10 bugs are regressors.
    bug_ids = [i // 2 for i in range(40)]
    labels = [1 if bug_id < 10 else 0 for bug_id in bug_ids]

    def items_gen(classes):
        model.train_bug_ids = bug_ids
        return iter(zip(range(40), labels))

    monkeypatch.setattr(model, "get_labels", lambda: ({}, [0, 1]))
    monkeypatch.setattr(model, "items_gen", items_gen)
    monkeypatch.setattr(
        model.extraction_pipeline,
        "transform",
        lambda X_gen: pd.DataFrame({"a": list(X_gen())}),
    )

    class Stop(Exception):
        pass

    captured = {}

    def cross_validate(clf, X, y, params, **kwargs):
        captured.update(X=X, y=y, params=params)
        raise Stop

    monkeypatch.setattr(bugbug.model, "cross_validate", cross_validate)
    with pytest.raises(Stop):
        model.train()

    # The folds get the same weights as the final model.
    weights = captured["params"]["estimator__sample_weight"]
    assert np.allclose(weights, model.get_sample_weights(captured["X"], captured["y"]))
    assert np.isclose(
        weights[captured["y"] == 1].sum(), weights[captured["y"] == 0].sum()
    )
