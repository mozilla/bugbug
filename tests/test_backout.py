# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from bugbug.models.backout import BackoutModel


def test_get_backout_labels():
    model = BackoutModel()
    classes, _ = model.get_labels()
    assert classes["c2b5cf7bde83db072fc206c24d1cab72354be727"] == 1
    assert classes["9d576871fd33bed006dcdccfba880a4ed591f870"] != 1
