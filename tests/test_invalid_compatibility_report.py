# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from bugbug.models.invalid_compatibility_report import InvalidCompatibilityReportModel


def test_get_invalid_labels():
    model = InvalidCompatibilityReportModel()
    classes, _ = model.get_labels()
    assert classes[70960]
    assert classes[70978]
    assert not classes[71052]
    assert not classes[71011]
