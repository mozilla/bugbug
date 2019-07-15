# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from bugbug.models.devdocneeded import DevDocNeededModel


def test_get_devdocneeded_labels():
    model = DevDocNeededModel()
    classes, _ = model.get_labels()
    assert classes[528988] == 0
    assert classes[1053944] == 1
    assert classes[1531080] == 1
