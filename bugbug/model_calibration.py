# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from sklearn.base import BaseEstimator, ClassifierMixin
from sklearn.calibration import CalibratedClassifierCV
from sklearn.model_selection import train_test_split


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
