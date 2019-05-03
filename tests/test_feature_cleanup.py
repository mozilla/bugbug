# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from bugbug import feature_cleanup


def test_url():
    tests = [
        (
            "This code lies in https://github.com/marco-c/bugbug",
            "This code lies in __URL__",
        ),
        (
            "Another url can be https://hg.mozilla.org/camino/ or https://google.com",
            "Another url can be __CODE_REFERENCE_URL__ or __URL__",
        ),
        (
            "Third example is https://searchfox.org and http://hg.mozilla.org",
            "Third example is __CODE_REFERENCE_URL__ and __CODE_REFERENCE_URL__",
        ),
        (
            "More generic links can be https://github.com/marco-c/bugbug , https://hg.mozilla.org/try/ and https://searchfox.org",
            "More generic links can be __URL__ , __CODE_REFERENCE_URL__ and __CODE_REFERENCE_URL__",
        ),
    ]
    for orig_text, cleaned_text in tests:
        assert feature_cleanup.url(orig_text) == cleaned_text


def test_fileref():
    tests = [
        (
            "Some random filenames are file1.py , file2.cpp and file3.json",
            "Some random filenames are __FILE_REFERENCE__ , __FILE_REFERENCE__ and __FILE_REFERENCE__",
        )
    ]
    for orig_text, cleaned_text in tests:
        assert feature_cleanup.fileref(orig_text) == cleaned_text


def test_responses():
    tests = [
        (
            "A response can be of the form>This is the comment\n",
            "A response can be of the form \n",
        ),
        (
            "Multiline responses can be>This is line 1\n>This is line2\n end of response",
            "Multiline responses can be \n \n end of response",
        ),
        ("Code snippet example is > + import bugbug\n", "Code snippet example is  \n"),
        (
            "Random responses >this is line1\n>this is line2\n>this is the final line",
            "Random responses  \n \n ",
        ),
    ]
    for orig_text, cleaned_text in tests:
        assert feature_cleanup.responses(orig_text) == cleaned_text


def test_hex():
    tests = [
        (
            "0 scdetour.dll scdetour.dll@0x2dd77",
            "0 scdetour.dll scdetour.dll@__HEX_NUMBER__",
        ),
        (
            "Some examples of hex numbers are 0x227c2 or 0x3fA2",
            "Some examples of hex numbers are __HEX_NUMBER__ or __HEX_NUMBER__",
        ),
    ]
    for orig_text, cleaned_text in tests:
        assert feature_cleanup.hex(orig_text) == cleaned_text


def test_dll():
    tests = [
        (
            "Crashing thread: 0 scdetour.dll scdetour.dll@0x2dd77",
            "Crashing thread: 0 __DLL_NAME__ __DLL_NAME__@0x2dd77",
        ),
        (
            "Crash in libxul.so@0x287ad36 | libxul.so@0x270c062",
            "Crash in libxul.so@0x287ad36 | libxul.so@0x270c062",
        ),
        ("Crash in libsystem_pthread.dylib@0x14fc", "Crash in __DLL_NAME__@0x14fc"),
        (
            "Crash in liblgpllibs.so@0x14fc exmpl.so@0xask ",
            "Crash in liblgpllibs.so@0x14fc __DLL_NAME__@0xask ",
        ),
        (
            "Crash in lgpllibs.dll@0x14fc exmpl.dll@0xask ",
            "Crash in lgpllibs.dll@0x14fc __DLL_NAME__@0xask ",
        ),
        (
            "Crash in libmozglue.dylib@0x14fc exmpl.dylib@0xask ",
            "Crash in libmozglue.dylib@0x14fc __DLL_NAME__@0xask ",
        ),
    ]
    for orig_text, cleaned_text in tests:
        assert feature_cleanup.dll(orig_text) == cleaned_text


def test_synonyms():
    tests = [
        (
            "I was in safemode, but the problem occurred in safe mode too",
            "I was in safemode, but the problem occurred in safemode too",
        ),
        ("SAFE MODE or SAFEMODE?", "safemode or safemode?"),
        ("are there str? steps to reproduce? repro steps?", "are there str? str? str?"),
        (
            "this is a use-after-free, also called uaf, also called use after free",
            "this is a uaf, also called uaf, also called uaf",
        ),
        ("found via address sanitizer or asan", "found via asan or asan"),
    ]
    for orig_text, cleaned_text in tests:
        assert feature_cleanup.synonyms(orig_text) == cleaned_text


def test_crash():
    tests = [
        (
            "This bug was filed from the Socorro interface and is report bp-ba7ff893-687f-4381-b430-ba66b0170628.",
            "This bug was filed from the Socorro interface and is report __CRASH_STATS_LINK__.",
        ),
        (
            "Random reports can be bp-ba7ff893-687f-4381-b430-ba66b0170628, bp-ab78f852-312c-4534-b576-ab5ba4341256.",
            "Random reports can be __CRASH_STATS_LINK__, __CRASH_STATS_LINK__.",
        ),
    ]
    for orig_text, cleaned_text in tests:
        assert feature_cleanup.crash(orig_text) == cleaned_text
