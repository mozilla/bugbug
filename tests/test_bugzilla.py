# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from bugbug import bugzilla


def test_get_bugs():
    all_bugs = {int(bug["id"]) for bug in bugzilla.get_bugs(include_invalid=True)}
    legitimate_bugs = {int(bug["id"]) for bug in bugzilla.get_bugs()}

    assert 1541482 in all_bugs
    assert 1541482 not in legitimate_bugs

    assert 1559674 in all_bugs
    assert 1559674 not in legitimate_bugs

    assert 1549207 in all_bugs
    assert 1549207 not in legitimate_bugs

    assert 1572747 in all_bugs
    assert 1572747 in legitimate_bugs
