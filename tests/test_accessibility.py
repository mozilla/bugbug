# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from bugbug.models.accessibility import AccessibilityModel


def test_get_access_labels():
    model = AccessibilityModel()
    classes, _ = model.get_labels()
    assert classes[1854279] == 1
    assert classes[1242711] == 1

    assert classes[1325215] == 0
    assert classes[1320039] == 0
