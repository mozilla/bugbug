# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from datetime import datetime, timezone

from bugbug.bug_snapshot import rollback
from bugbug.models.defect import DefectModel
from bugbug.models.regression import RegressionModel


def test_get_defect_labels():
    model = DefectModel()
    classes, _ = model.get_labels()
    assert classes[1042414] == 1
    assert classes[1049816] != 1


def _make_bug(regression_keyword_added_on):
    return {
        "id": 1,
        "product": "Core",
        "creator": "reporter@example.com",
        "creation_time": "2026-01-01T00:00:00Z",
        "keywords": ["regression"],
        "comments": [
            {
                "count": 0,
                "id": 1,
                "text": "Something broke",
                "author": "reporter@example.com",
                "creation_time": "2026-01-01T00:00:00Z",
            },
            {
                "count": 1,
                "id": 2,
                "text": "Some more details",
                "author": "reporter@example.com",
                "creation_time": "2026-01-01T12:00:00Z",
            },
            {
                "count": 2,
                "id": 3,
                "text": "This is a regression from bug 123",
                "author": "dev@example.com",
                "creation_time": regression_keyword_added_on,
            },
        ],
        "attachments": [],
        "flags": [],
        "history": [
            {
                "when": regression_keyword_added_on,
                "who": "dev@example.com",
                "changes": [
                    {"field_name": "keywords", "added": "regression", "removed": ""},
                ],
            },
        ],
    }


def _date(day):
    return datetime(2026, 1, day, tzinfo=timezone.utc)


def test_training_snapshot_before_regression_label():
    model = DefectModel()
    bug = _make_bug("2026-01-02T00:00:00Z")
    assert model.get_rollback_point(bug, 1) == _date(2)

    bug = rollback(bug, before=model.get_rollback_point(bug, 1))
    assert bug["keywords"] == []
    assert [c["count"] for c in bug["comments"]] == [0, 1]


def test_training_snapshot_few_days_after_creation():
    model = DefectModel()
    bug = _make_bug("2026-01-10T00:00:00Z")
    assert model.get_rollback_point(bug, 1) == _date(4)

    bug = rollback(bug, before=model.get_rollback_point(bug, 1))
    assert bug["keywords"] == []
    assert [c["count"] for c in bug["comments"]] == [0, 1]


def test_regression_training_snapshot():
    model = RegressionModel()

    # Regressions are rolled back to right before they were identified as such.
    assert model.get_rollback_point(_make_bug("2026-01-10T00:00:00Z"), 1) == _date(10)

    # Non-regressions are rolled back to the age at which regressions are identified.
    bug = _make_bug("2026-01-10T00:00:00Z")
    bug["creation_time"] = "2026-01-05T00:00:00Z"
    assert model.get_rollback_point(bug, 0) == _date(14)
