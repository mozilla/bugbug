# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from bugbug import bug_features


def test_cleanup_url():
    tests = [
        ('This code lies in https://github.com/marco-c/bugbug', 'This code lies in URL'),
    ]
    for orig_text, cleaned_text in tests:
        assert bug_features.cleanup_url(orig_text) == cleaned_text


def test_cleanup_fileref():
    tests = [
        ('Some random filenames are file1.py , file2.cpp and file3.json', 'Some random filenames are FILE_REFERENCE , FILE_REFERENCE and FILE_REFERENCE'),
    ]
    for orig_text, cleaned_text in tests:
        assert bug_features.cleanup_fileref(orig_text) == cleaned_text
