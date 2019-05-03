# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.


import json

from bugbug.bug_snapshot import rollback


def test_bug_snapshot(get_fixture_path):
    mock_bugs_db_path = get_fixture_path("bugs.json")

    with open(mock_bugs_db_path, "r") as mock_bug_db:

        for i, line in enumerate(mock_bug_db):
            bug = json.loads(line)

            print(bug["id"])
            print(i)

            rollback(bug, None, False)
