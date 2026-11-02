# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import numpy as np

from bugbug.models import spamcomment
from bugbug.models.spamcomment import SpamCommentModel


def make_comment(comment_id, count, creator, text="Some text", tags=()):
    return {
        "id": comment_id,
        "count": count,
        "creator": creator,
        "text": text,
        "tags": list(tags),
    }


def test_get_spamcomment_labels(monkeypatch):
    bugs = [
        {
            "id": 1,
            "comments": [
                make_comment(10, 0, "reporter@example.com", tags=["spam"]),
                make_comment(11, 1, "spammer@example.com", tags=["spam"]),
                make_comment(12, 2, "user@example.com"),
                make_comment(13, 3, "dev@example.com", tags=["spam"]),
                make_comment(14, 4, "someone@mozilla.com"),
                make_comment(15, 5, "user@example.com", text="(comment removed)"),
            ],
        }
    ]
    monkeypatch.setattr(spamcomment.bugzilla, "get_bugs", lambda: iter(bugs))
    monkeypatch.setattr(
        spamcomment.repository,
        "get_commits",
        lambda **kwargs: iter([{"author_email": "dev@example.com"}]),
    )
    monkeypatch.setattr(
        SpamCommentModel,
        "_SpamCommentModel__download_older_bugs_with_spam_comments",
        staticmethod(lambda: None),
    )

    model = SpamCommentModel()
    classes, labels = model.get_labels()

    # The first comment (the bug description), comments by commit authors or
    # Mozilla accounts, and removed comments are skipped.
    assert classes == {11: 1, 12: 0}
    assert labels == [0, 1]
    assert model.commit_emails == {"dev@example.com"}


def test_spamcomment_overwrite_classes():
    model = SpamCommentModel()
    model.commit_emails = {"dev@example.com"}

    bug = {"id": 1}
    items = [
        (bug, make_comment(11, 1, "spammer@example.com")),
        (bug, make_comment(12, 2, "dev@example.com")),
        (bug, make_comment(13, 3, "someone@mozilla.com")),
    ]

    classes = model.overwrite_classes(items, np.array([1, 1, 1]), False)
    assert list(classes) == [1, 0, 0]

    probabilities = np.array([[0.1, 0.9], [0.2, 0.8], [0.3, 0.7]])
    classes = model.overwrite_classes(items, probabilities, True)
    assert classes.tolist() == [[0.1, 0.9], [1.0, 0.0], [1.0, 0.0]]
