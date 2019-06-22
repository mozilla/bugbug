# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.


from bugbug.models.regression import RegressionModel


def test_get_regression_labels():
    model = RegressionModel()
    classes, _ = model.get_labels()
    assert classes[447581] == 0
    assert classes[518272] == 1
