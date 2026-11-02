# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import pickle

import pytest

from bugbug import comment_features

SAFE_DOMAINS = {"mozilla.org"}


@pytest.mark.parametrize(
    "text, expected",
    [
        ("No links here.", (-1, -1, -1)),
        ("See https://developer.mozilla.org for details.", (-1, -1, -1)),
        ("https://spam.com", (0, 16, 0)),
        ("Buy now at https://spam.com", (11, 27, 0)),
        ("  https://spam.com is great  ", (0, 16, 9)),
        ("(https://spam.com) and www.other.net!", (1, 36, 1)),
        ("Read https://mozilla.org then www.spam.com today", (30, 42, 6)),
    ],
)
def test_unknown_link_positions(text, expected):
    feature = comment_features.UnknownLinkPositions(SAFE_DOMAINS)
    result = feature(({}, {"text": text}))

    assert (
        result["Start of the first unknown link"],
        result["End of the last unknown link"],
        result["# of characters after the last unknown link"],
    ) == expected


def test_comment_creator_is_bug_creator():
    feature = comment_features.CommentCreatorIsBugCreator()

    assert feature(({"creator": "a@example.com"}, {"creator": "a@example.com"}))
    assert not feature(({"creator": "a@example.com"}, {"creator": "b@example.com"}))


def test_time_features():
    # 2024-03-02 was a Saturday, and the 62nd day of the year.
    comment = {"creation_time": "2024-03-02T13:45:00Z"}

    assert comment_features.HourOfDay()(({}, comment)) == 13
    assert comment_features.Weekday()(({}, comment)) == 6
    assert comment_features.DayOfYear()(({}, comment)) == 62


def test_commenter_experience():
    def comment(comment_id, creator, creation_time):
        return {"id": comment_id, "creator": creator, "creation_time": creation_time}

    # The comments are not in time order across bugs on purpose.
    bug1 = {
        "id": 1,
        "comments": [
            comment(10, "a@example.com", "2024-05-01T10:00:00Z"),
            comment(11, "b@example.com", "2024-05-02T10:00:00Z"),
            comment(12, "a@example.com", "2024-05-03T10:00:00Z"),
        ],
    }
    bug2 = {
        "id": 2,
        "comments": [
            comment(20, "a@example.com", "2020-01-01T10:00:00Z"),
        ],
    }

    feature = comment_features.CommenterExperience()
    # Bug 1 appears twice, its comments must only be counted once.
    feature.fit(
        [
            (bug1, bug1["comments"][1]),
            (bug1, bug1["comments"][2]),
            (bug2, bug2["comments"][0]),
        ]
    )

    assert feature((bug2, bug2["comments"][0])) == 0
    assert feature((bug1, bug1["comments"][0])) == 1
    assert feature((bug1, bug1["comments"][2])) == 2
    assert feature((bug1, bug1["comments"][1])) == 0

    # A new comment, posted after the training data.
    new_comment = comment(30, "a@example.com", "2025-01-01T10:00:00Z")
    assert feature(({}, new_comment)) == 3

    # A commenter that was never seen before.
    unknown = comment(31, "new@example.com", "2025-01-01T10:00:00Z")
    assert feature(({}, unknown)) == 0

    # The saved feature only keeps the number of comments of each commenter.
    loaded = pickle.loads(pickle.dumps(feature))
    assert loaded.comment_times == {"a@example.com": 3, "b@example.com": 1}
    assert loaded(({}, new_comment)) == 3
    assert loaded(({}, unknown)) == 0

    # The original feature still has the full timestamps.
    assert feature((bug1, bug1["comments"][0])) == 1
