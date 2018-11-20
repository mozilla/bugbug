# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from bugbug import labels


def test_get_tracking_labels():
    classes = labels.get_tracking_labels()
    assert not classes[1042138]
    assert classes[1042096]


def test_get_labels():
    classes = labels.get_bugbug_labels()
    # labels from bug_nobug.csv
    assert classes[1087488]
    assert not classes[1101825]
    # labels from regression_bug_nobug.csv
    assert not classes[447581]  # nobug
    assert classes[518272]  # regression
    assert classes[528988]  # bug_unknown_regression
    assert classes[1037762]  # bug_no_regression
