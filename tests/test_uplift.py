# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from bugbug.models.uplift import UpliftModel


def test_get_uplift_labels():
    model = UpliftModel()
    classes, _ = model.get_labels()
    assert classes[1364870] == 1
    assert classes[1350663] != 1
