# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import numpy as np

from bugbug.models.bugtype import BugTypeModel


def test_get_bugtype_labels():

    model = BugTypeModel()
    classes, keyword_list = model.get_labels()

    assert np.array_equal(classes[1319957], np.zeros(4))

    target = np.zeros(4)
    target[keyword_list.index("crash")] = 1
    assert np.array_equal(classes[1319973], target)

    target = np.zeros(4)
    target[keyword_list.index("memory")] = 1
    assert np.array_equal(classes[1325215], target)

    target = np.zeros(4)
    target[keyword_list.index("performance")] = 1
    assert np.array_equal(classes[1320195], target)

    target = np.zeros(4)
    target[keyword_list.index("security")] = 1
    assert np.array_equal(classes[1320039], target)
