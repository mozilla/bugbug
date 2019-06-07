# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.


from bugbug.models.stepstoreproduce import StepsToReproduceModel


def test_get_labels():
    model = StepsToReproduceModel()
    classes, _ = model.get_labels()
    assert classes[1488310]
    assert not classes[1372243]
    assert 1319973 not in classes
