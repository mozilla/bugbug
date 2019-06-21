# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from bugbug.models.backout import BackoutModel


def test_get_backout_labels():
    model = BackoutModel()
    classes, _ = model.get_labels()
    assert classes["e2a02b08089b0bd0c18ceac0b2eb1e3888d56dc2"] == 1
    assert classes["9d576871fd33bed006dcdccfba880a4ed591f870"] != 1
