# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from sklearn.isotonic import IsotonicRegression
from sklearn.model_selection import train_test_split


class IsotonicRegressionCalibrator:
    def __init__(self, model):
        self.model = model
        self.calibrator = IsotonicRegression()

    def split_data(self, X, y):
        X_train_val, X_test, y_train_val, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42
        )
        X_train, X_val, y_train, y_val = train_test_split(
            X_train_val, y_train_val, test_size=0.2, random_state=42
        )
        return X_train, X_val, X_test, y_train, y_val, y_test

    def fit(self, X_train, y_train, X_val, y_val):
        self.model.fit(X_train, y_train)
        self.calibrate(X_val, y_val)

    def predict(self, X):
        return self.calibrator.predict(self.model.predict(X))

    def calibrate(self, X_val, y_val):
        self.calibrator.fit(self.model.predict(X_val), y_val)

    def train(self, X, y):
        X_train, X_val, X_test, y_train, y_val, y_test = self.split_data(X, y)
        self.fit(X_train, y_train, X_val, y_val)
        return X_train, X_val, X_test, y_train, y_val, y_test
