# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

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
