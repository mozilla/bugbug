# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import pytest

from bugbug.tools.core.exceptions import RunawayGenerationError
from bugbug.tools.core.text import check_runaway_generation, find_runaway_repetition


def test_short_text_passes():
    text = "## Summary\n\nThis is a perfectly normal, concise review comment."
    # Should not raise.
    check_runaway_generation(text)


def test_empty_text_passes():
    check_runaway_generation("")


def test_single_word_repetition_raises():
    # The exact failure mode from issue #5995.
    text = "The change is good. " + "prevent " * 5000
    with pytest.raises(RunawayGenerationError):
        check_runaway_generation(text)


def test_phrase_repetition_raises():
    text = "Here is the issue: " + "not converting " * 5000
    with pytest.raises(RunawayGenerationError):
        check_runaway_generation(text)


def test_excessive_length_raises():
    text = "a. " * 100_000  # No single chunk repeats, but it is enormous.
    with pytest.raises(RunawayGenerationError):
        check_runaway_generation(text)


def test_legitimate_short_repetition_passes():
    # A handful of repeated words is not a runaway loop and should be allowed.
    check_runaway_generation("no no no, this is fine")


def test_custom_max_length():
    text = "abcdefghijklmnopqrstuvwxyz" * 4
    with pytest.raises(RunawayGenerationError):
        check_runaway_generation(text, max_length=10)


def test_error_message_includes_label():
    text = "x " + "loop " * 100
    with pytest.raises(RunawayGenerationError, match="patch summary"):
        check_runaway_generation(text, label="patch summary")


def test_find_runaway_repetition_returns_offset():
    text = "Intro. " + "spam " * 50
    offset = find_runaway_repetition(text)
    # The loop starts right after the intro (the detector may include the
    # preceding space as part of the repeating chunk).
    assert offset is not None
    assert offset in (len("Intro."), len("Intro. "))


def test_find_runaway_repetition_returns_none_for_clean_text():
    assert find_runaway_repetition("A clean and normal summary.") is None
