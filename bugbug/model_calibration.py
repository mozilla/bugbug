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

    def split_data(self, X, y, test_size=0.2, random_state=42):
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=test_size, random_state=random_state
        )
        return X_train, X_test, y_train, y_test

    def fit(self, X_train, y_train):
        X_train, X_val, y_train, y_val = self.split_data(X_train, y_train)
        self.base_clf.fit(X_train, y_train)

        self.calibrated_clf.fit(X_val, y_val)

    def predict(self, X):
        return self.calibrated_clf.predict(X)

    def predict_proba(self, X_val):
        return self.calibrated_clf.predict_proba(X_val)

    def calibrate(self, X_val, y_val):
        self.calibrated_clf.cv = "prefit"
        self.calibrated_clf.method = "isotonic"
        self.calibrated_clf.fit(X_val, y_val)

    def train(self, X, y):
        X_train, X_test, y_train, y_test = self.split_data(X, y)
        self.fit(X_train, y_train)
