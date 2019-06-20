# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from bugbug.models.backout import BackoutModel


def test_get_backout_labels():
    model = BackoutModel()
    classes, _ = model.get_labels()
    assert not classes[1101825]
    assert classes[1042096]