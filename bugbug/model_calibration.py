# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Calibration of model outputs to observed rates."""

from typing import Sequence

from sklearn.base import BaseEstimator, ClassifierMixin
from sklearn.calibration import CalibratedClassifierCV
from sklearn.model_selection import train_test_split


def fit_isotonic_breakpoints(
    x: Sequence[float], rates: Sequence[float], weights: Sequence[float]
) -> tuple[list[float], list[float]]:
    """Weighted isotonic regression of observed rates on a score, as breakpoints.

    The regression is clipped to [0, 1] and pools adjacent violators; the
    breakpoints scikit-learn keeps reproduce its predictions through
    ``numpy.interp`` without the fitted estimator, the nearest end holding
    outside them.
    """
    from sklearn.isotonic import IsotonicRegression

    regression = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
    regression.fit(x, rates, sample_weight=weights)
    return (
        [float(value) for value in regression.X_thresholds_],
        [float(value) for value in regression.y_thresholds_],
    )


class IsotonicRegressionCalibrator(BaseEstimator, ClassifierMixin):
    def __init__(self, base_clf):
        self.base_clf = base_clf
        self.calibrated_clf = CalibratedClassifierCV(
            base_clf, cv="prefit", method="isotonic"
        )

    def fit(self, X_train, y_train):
        X_train, X_val, y_train, y_val = train_test_split(
            X_train, y_train, test_size=0.2, random_state=42
        )
        self.base_clf.fit(X_train, y_train)
        self.calibrated_clf.fit(X_val, y_val)

    def predict(self, X):
        return self.calibrated_clf.predict(X)

    def predict_proba(self, X):
        return self.calibrated_clf.predict_proba(X)

    @property
    def n_features_in_(self):
        return self.base_clf.n_features_in_
