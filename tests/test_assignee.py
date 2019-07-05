# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from bugbug.models import assignee
from bugbug.models.assignee import AssigneeModel


def test_get_assignee_labels():
    assignee.MINIMUM_ASSIGNMENTS = 1
    model = AssigneeModel()
    classes, _ = model.get_labels()
    assert len(classes) != 0
    assert classes[1320039] == "gijskruitbosch+bugs@gmail.com"
    assert classes[1045018] == "padenot@mozilla.com"
    assert 1319973 not in classes
