# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from bugbug import bugzilla
from bugbug.models.qaneeded import QANeededModel


def test_get_qaneeded_labels():
    model = QANeededModel()
    classes, _ = model.get_labels()
    assert not classes[1389220]
    assert classes[1389223], "Bug should contain qawanted in a field"
    assert classes[1390433], "Bug should contain qe-verify in a field"


def test_rollback():
    model = QANeededModel()

    histories = {}
    for bug in bugzilla.get_bugs():
        histories[int(bug["id"])] = bug["history"]

    def rollback_point(bug_id):
        count = 0
        for history in histories[bug_id]:
            for change in history["changes"]:
                if model.rollback(change):
                    return count
                count += 1
        return count

    assert rollback_point(1390433) == 35, (
        "A bug field should start with qawanted or qe-verify"
    )
    assert rollback_point(1389136) == 9, (
        "A bug field should start with qawanted or qe-verify"
    )

    assert rollback_point(1388990) == 29
    assert rollback_point(1389223) == 8
