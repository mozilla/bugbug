# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from sklearn.isotonic import IsotonicRegression
from sklearn.model_selection import train_test_split


# Wrapper class to calibrate a model
class IsotonicRegressionCalibrator:
    def __init__(self, model):
        self.model = model
        self.calibrated = False
        self.X_train, self.X_val, self.X_test = None, None, None
        self.y_train, self.y_val, self.y_test = None, None, None
        self.calibrator = None

    def split_data(self, X, y):
        self.X_train, self.X_test, self.y_train, self.y_test = train_test_split(
            X, y, test_size=0.2, random_state=42
        )
        self.X_train, self.X_val, self.y_train, self.y_val = train_test_split(
            self.X_train, self.y_train, test_size=0.2, random_state=42
        )

    def fit(self):
        self.model.fit(self.X_train, self.y_train)
        self.calibrate()

    def predict(self, X):
        if self.calibrated:
            return self.calibrator.predict(self.model.predict(X))
        else:
            return self.model.predict(X)

    # Calibrate the model
    def calibrate(self):
        if not self.calibrated:
            self.calibrator = IsotonicRegression()
            self.calibrator.fit(self.model.predict(self.X_val), self.y_val)
            self.calibrated = True

    def train(self, X, y):
        self.split_data(X, y)
        self.fit()
