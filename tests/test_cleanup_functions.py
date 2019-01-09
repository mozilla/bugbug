# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from bugbug import bug_features


def test_cleanup_url():
    tests = [
        ('This code lies in https://github.com/marco-c/bugbug', 'This code lies in __URL__'),
        ('Another url can be https://hg.mozilla.org/camino/ or https://google.com', 'Another url can be __CODE_REFERENCE_URL__ or __URL__'),
        ('Third example is https://searchfox.org and http://hg.mozilla.org', 'Third example is __CODE_REFERENCE_URL__ and __CODE_REFERENCE_URL__'),
        ('More generic links can be https://github.com/marco-c/bugbug , https://hg.mozilla.org/try/ and https://searchfox.org', 'More generic links can be __URL__ , __CODE_REFERENCE_URL__ and __CODE_REFERENCE_URL__')
    ]
    for orig_text, cleaned_text in tests:
        assert bug_features.cleanup_url(orig_text) == cleaned_text


def test_cleanup_fileref():
    tests = [
        ('Some random filenames are file1.py , file2.cpp and file3.json', 'Some random filenames are __FILE_REFERENCE__ , __FILE_REFERENCE__ and __FILE_REFERENCE__'),
    ]
    for orig_text, cleaned_text in tests:
        assert bug_features.cleanup_fileref(orig_text) == cleaned_text


def test_cleanup_hex():
    tests = [
        ('0 scdetour.dll scdetour.dll@0x2dd77', '0 scdetour.dll scdetour.dll@__HEX_NUMBER__'),
    ]
    for orig_text, cleaned_text in tests:
        assert bug_features.cleanup_hex(orig_text) == cleaned_text


def test_cleanup_synonyms():
    tests = [
        ('I was in safemode, but the problem occurred in safe mode too', 'I was in safemode, but the problem occurred in safemode too'),
        ('SAFE MODE or SAFEMODE?', 'safemode or safemode?'),
        ('are there str? steps to reproduce? repro steps?', 'are there str? str? str?'),
        ('this is a use-after-free, also called uaf, also called use after free', 'this is a uaf, also called uaf, also called uaf'),
        ('found via address sanitizer or asan', 'found via asan or asan'),
    ]
    for orig_text, cleaned_text in tests:
        assert bug_features.cleanup_synonyms(orig_text) == cleaned_text
