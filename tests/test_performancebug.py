# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from bugbug.models.performancebug import PerformanceBugModel


def test_get_performancebug_labels():
    model = PerformanceBugModel()
    classes, _ = model.get_labels()
    assert classes[1856574] == 1
    assert classes[1633318] == 1
    assert classes[185598] == 1
    assert classes[1355978] == 1
    assert classes[1481519] == 1
    assert classes[1543990] == 0
    assert classes[1488738] == 0
    assert classes[600692] == 0
    assert classes[1411253] == 0
    assert classes[1609878] == 0
