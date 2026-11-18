# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.


from bugbug import bugzilla
from bugbug.bug_snapshot import rollback


def test_bug_snapshot():
    for i, bug in enumerate(bugzilla.get_bugs()):
        print(bug["id"])
        print(i)

        rollback(bug, do_assert=True)


def test_rollback_normalizes_timestamps():
    # Bugzilla used to return history timestamps as ISO 8601 with a "Z" suffix,
    # and now returns them as "YYYY-MM-DD HH:MM:SS".
    for fmt in (lambda val: val.replace(" ", "T") + "Z", lambda val: val):
        bug = {
            "id": 1,
            "product": "Core",
            "creator": "reporter@example.com",
            "creation_time": "2026-01-01T00:00:00Z",
            "cf_last_resolved": "2026-03-01T12:00:00Z",
            "cf_due_date": "2026-04-01T00:00:00Z",
            "comments": [
                {
                    "count": 0,
                    "id": 1,
                    "text": "",
                    "author": "reporter@example.com",
                    "creation_time": "2026-01-01T00:00:00Z",
                }
            ],
            "attachments": [],
            "flags": [],
            "history": [
                {
                    "when": "2026-02-01T12:00:00Z",
                    "who": "dev@example.com",
                    "changes": [
                        {
                            "field_name": "cf_last_resolved",
                            "added": fmt("2026-02-01 12:00:00"),
                            "removed": "",
                        },
                    ],
                },
                {
                    "when": "2026-03-01T12:00:00Z",
                    "who": "dev@example.com",
                    "changes": [
                        {
                            "field_name": "cf_last_resolved",
                            "added": fmt("2026-03-01 12:00:00"),
                            "removed": fmt("2026-02-01 12:00:00"),
                        },
                        {
                            "field_name": "cf_due_date",
                            "added": fmt("2026-04-01 00:00:00"),
                            "removed": "",
                        },
                    ],
                },
            ],
        }

        rollback(
            bug,
            when=lambda change: (
                change["field_name"] == "cf_last_resolved" and change["added"]
            ),
            do_assert=True,
        )

        assert bug["cf_last_resolved"] == "2026-02-01T12:00:00Z"
        assert bug["cf_due_date"] is None
