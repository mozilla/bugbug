# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from bugbug.models.bug import BugModel


def test_get_bug_labels():
    model = BugModel()
    classes = model.get_labels()
    # labels from bug_nobug.csv
    assert classes[1087488]
    assert not classes[1101825]
    # labels from regression_bug_nobug.csv
    assert not classes[447581]  # nobug
    assert classes[518272]  # regression
    assert classes[528988]  # bug_unknown_regression
    assert classes[1037762]  # bug_no_regression
    # labels from defectenhancementtask.csv
    assert not classes[1488307]  # task
    assert classes[1488310]  # defect
    assert not classes[1531080]  # enhancement
