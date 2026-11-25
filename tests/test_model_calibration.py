# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import numpy as np
import pytest

from bugbug.model_calibration import fit_isotonic_breakpoints


def test_isotonic_breakpoints_pool_violators_and_interpolate() -> None:
    # The rate dips at 0.6, so 0.6 and 0.7 are pooled by weight: (20 + 15) / 2000.
    x, y = fit_isotonic_breakpoints(
        [0.5, 0.6, 0.7, 0.95], [0.01, 0.02, 0.015, 0.3], [1000, 1000, 1000, 100]
    )
    assert x[0] == 0.5 and x[-1] == 0.95
    assert (
        y == sorted(y) and y[0] == pytest.approx(0.01) and y[-1] == pytest.approx(0.3)
    )
    assert np.interp(0.6, x, y) == pytest.approx(0.0175)
    assert np.interp(0.7, x, y) == pytest.approx(0.0175)
    # Beyond the breakpoints the ends hold; between two, the line between them.
    assert np.interp(0.1, x, y) == pytest.approx(0.01)
    assert np.interp(1.0, x, y) == pytest.approx(0.3)
    between = np.interp([0.825], x, y)
    assert 0.0175 < between[0] < 0.3


def test_calibrator_methods_keep_or_tie_the_ranking() -> None:
    import xgboost

    from bugbug.model_calibration import IsotonicRegressionCalibrator

    rng = np.random.default_rng(0)
    X = rng.normal(size=(2000, 3))
    y = (X[:, 0] + rng.normal(size=2000) > 0).astype(int)

    distinct = {}
    for method in ("isotonic", "sigmoid"):
        calibrator = IsotonicRegressionCalibrator(
            xgboost.XGBClassifier(n_estimators=20), method=method
        )
        assert calibrator.get_params()["method"] == method
        calibrator.fit(X, y)
        probs = calibrator.predict_proba(X)[:, 1]
        assert ((probs >= 0) & (probs <= 1)).all()
        distinct[method] = len(np.unique(probs))

    # Isotonic calibration is a step function, sigmoid calibration is strictly monotonic.
    assert distinct["sigmoid"] > 10 * distinct["isotonic"]


def test_calibration_weight() -> None:
    import xgboost

    from bugbug.model_calibration import IsotonicRegressionCalibrator

    rng = np.random.default_rng(0)
    X = rng.normal(size=(4000, 3))
    # 10% positives.
    y = (X[:, 0] + rng.normal(size=4000) > 1.8).astype(int)
    balanced = np.where(y == 1, 0.5 / y.mean(), 0.5 / (1 - y.mean()))

    calibrator = IsotonicRegressionCalibrator(
        xgboost.XGBClassifier(n_estimators=20), method="sigmoid"
    )
    # Calibrating with class-balanced weights inflates the risks...
    calibrator.fit(X, y, sample_weight=balanced)
    assert calibrator.predict_proba(X)[:, 1].mean() > 0.3
    # ...unlike calibrating without them.
    calibrator.fit(X, y, sample_weight=balanced, calibration_weight=np.ones(len(y)))
    assert abs(calibrator.predict_proba(X)[:, 1].mean() - y.mean()) < 0.03
