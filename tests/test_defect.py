# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from bugbug.models.defect import DefectModel


def test_get_defect_labels():
    model = DefectModel()
    classes, _ = model.get_labels()
    assert classes[1042414] == 1
    assert classes[1049816] != 1
