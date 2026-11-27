# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from datetime import datetime, timezone
from logging import INFO, basicConfig, getLogger

from bugbug import model
from bugbug.models import MODELS, get_model_class

basicConfig(level=INFO)
logger = getLogger(__name__)


def test_import_all_models():
    """Try loading all defined models to ensure that their full qualified names are still good.

    Iterates over all known model names and attempts to import them.
    """
    for model_name in MODELS:
        logger.info("Try loading model %s", model_name)
        get_model_class(model_name)


def test_component_is_bugmodel():
    model_class = get_model_class("component")
    assert issubclass(model_class, model.BugModel)
    model_class = get_model_class("regression")
    assert issubclass(model_class, model.BugModel)


def test_backout_is_commitmodel():
    model_class = get_model_class("backout")
    assert issubclass(model_class, model.CommitModel)


def test_comment_model_items_gen(monkeypatch):
    bugs = [
        {"id": 1, "comments": [{"id": 11}, {"id": 12}]},
        {"id": 2, "comments": []},
    ]
    monkeypatch.setattr(model.bugzilla, "get_bugs", lambda: iter(bugs))

    assert list(model.CommentModel().items_gen({11: "spam"})) == [
        ((bugs[0], bugs[0]["comments"][0]), "spam")
    ]


def _make_bug():
    return {
        "id": 1,
        "product": "Core",
        "creator": "reporter@example.com",
        "creation_time": "2026-01-01T00:00:00Z",
        "keywords": ["perf", "regression"],
        "severity": "S3",
        "comments": [
            {
                "count": i,
                "id": i + 1,
                "text": f"comment {i}",
                "author": "reporter@example.com",
                "creation_time": when,
            }
            for i, when in enumerate(
                ["2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z", "2026-01-05T00:00:00Z"]
            )
        ],
        "attachments": [],
        "flags": [],
        "history": [
            {
                "when": "2026-01-02T00:00:00Z",
                "who": "dev@example.com",
                "changes": [
                    {"field_name": "severity", "added": "S3", "removed": "--"},
                ],
            },
            {
                "when": "2026-01-05T00:00:00Z",
                "who": "dev@example.com",
                "changes": [
                    {"field_name": "keywords", "added": "regression", "removed": ""},
                ],
            },
        ],
    }


def _is_severity_change(change):
    return change["field_name"] == "severity"


def test_bug_model_rollback_training_items():
    class TestBugModel(model.BugModel):
        def get_rollback_point(self, item, label):
            return label

    bugs = list(
        model.BugModel().rollback_training_items(
            [
                (_make_bug(), None),
                (_make_bug(), "creation"),
                (_make_bug(), datetime(2026, 1, 3, tzinfo=timezone.utc)),
                (_make_bug(), _is_severity_change),
            ]
        )
    )
    # By default, bugs are not rolled back.
    assert [len(bug["comments"]) for bug, _ in bugs] == [3, 3, 3, 3]

    bugs = [
        bug
        for bug, _ in TestBugModel().rollback_training_items(
            [
                (_make_bug(), None),
                (_make_bug(), "creation"),
                (_make_bug(), datetime(2026, 1, 3, tzinfo=timezone.utc)),
                (_make_bug(), _is_severity_change),
            ]
        )
    ]
    assert [bug["keywords"] for bug in bugs] == [
        ["perf", "regression"],
        ["perf"],
        ["perf"],
        ["perf"],
    ]
    assert [bug["severity"] for bug in bugs] == ["S3", "--", "S3", "S3"]
    assert [len(bug["comments"]) for bug in bugs] == [3, 1, 2, 2]
