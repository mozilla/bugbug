# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from bugbug import bugzilla
from bugbug.models.qaneeded import QANeededModel


def test_get_qaneeded_labels():
    model = QANeededModel()
    classes = model.get_labels()
    assert not classes[1389220]
    assert classes[1389223], 'Bug should contain qawanted in a field'
    assert classes[1390433], 'Bug should contain qe-verify in a field'


def test_rollback():
    model = QANeededModel()

    histories = {}
    for bug in bugzilla.get_bugs():
        histories[int(bug['id'])] = bug

    def contains_field(bug_id):
        count = False
        for history in histories[bug_id]['history']:
            for change in history['changes']:
                if model.rollback(change):
                    count = True
                    break
        return count

    assert contains_field(1390433), 'A bug field should start with qawanted or qe-verify'
    assert not contains_field(1389136), 'A bug field should start with qawanted or qe-verify'
