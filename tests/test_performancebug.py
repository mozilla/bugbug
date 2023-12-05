# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from bugbug.models.performancebug import PerformanceBugModel


def test_get_performancebug_labels():
    model = PerformanceBugModel()
    classes, _ = model.get_labels()
    assert classes[1320195] == 1
    assert classes[447581] == 1
    assert classes[1388990] == 0
    assert classes[1389136] == 0
