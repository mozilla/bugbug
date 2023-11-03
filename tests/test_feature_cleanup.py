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
        assert feature_cleanup.url()(orig_text) == cleaned_text


def test_fileref():
    tests = [
        (
            "Some random filenames are file1.py , file2.cpp and file3.json",
            "Some random filenames are __FILE_REFERENCE__ , __FILE_REFERENCE__ and __FILE_REFERENCE__",
        )
    ]
    for orig_text, cleaned_text in tests:
        assert feature_cleanup.fileref()(orig_text) == cleaned_text


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
        assert feature_cleanup.responses()(orig_text) == cleaned_text


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
        assert feature_cleanup.hex()(orig_text) == cleaned_text


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
        assert feature_cleanup.dll()(orig_text) == cleaned_text


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
        assert feature_cleanup.synonyms()(orig_text) == cleaned_text


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
        assert feature_cleanup.crash()(orig_text) == cleaned_text


def test_clean_compatibility_report_description():
    tests = [
        (
            '<!-- @browser: Firefox 117.0 -->\n<!-- @ua_header: Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/117.0 -->\n<!-- @reported_with: unknown -->\n<!-- @public_url: https://github.com/webcompat/web-bugs/issues/126685 -->\n\n**URL**: https://www.lequipe.fr/explore/video/la-course-en-tete/20177528\n\n**Browser / Version**: Firefox 117.0\n**Operating System**: Windows 10\n**Tested Another Browser**: Yes Chrome\n\n**Problem type**: Video or audio doesn\'t play\n**Description**: Media controls are broken or missing\n**Steps to Reproduce**:\nVideo is starting but we cannot use the video panel control. It working on Brave.\r\n<details>\r\n      <summary>View the screenshot</summary>\r\n      <img alt="Screenshot" src="https://webcompat.com/uploads/2023/9/501af310-e646-4b2c-8eb9-7f21ce8725fe.jpg">\r\n      </details>\n\n<details>\n<summary>Browser Configuration</summary>\n<ul>\n  <li>None</li>\n</ul>\n</details>\n\n_From [webcompat.com](https://webcompat.com/) with ❤️_',
            "Video is starting but we cannot use the video panel control. It working on Brave.",
        ),
        (
            "<!-- @browser: Firefox Mobile 120.0 -->\n<!-- @ua_header: Mozilla/5.0 (Android 10; Mobile; rv:120.0) Gecko/120.0 Firefox/120.0 -->\n<!-- @reported_with: unknown -->\n<!-- @public_url: https://github.com/webcompat/web-bugs/issues/128961 -->\n\n**URL**: https://www.jianshu.com/p/ba52ec38ac51\n\n**Browser / Version**: Firefox Mobile 120.0\n**Operating System**: Android 10\n**Tested Another Browser**: Yes Edge\n\n**Problem type**: Something else\n**Description**: Couldn't scroll down\n**Steps to Reproduce**:\nScroll down the page, then scroll to top, scroll down again, the page couldn't scroll (will always back to top). \n\n<details>\n<summary>Browser Configuration</summary>\n<ul>\n  <li>None</li>\n</ul>\n</details>\n\n_From [webcompat.com](https://webcompat.com/) with ❤️_",
            "Couldn't scroll down\n Scroll down the page, then scroll to top, scroll down again, the page couldn't scroll (will always back to top).",
        ),
        (
            '**URL**:\r\nhttps://samarabags.com/collections/all-bags/products/the-jewelry-box?variant=40390455820322\r\n\r\n**Browser/Version**:\r\nFirefox 112.0.2\r\n\r\n**Operating System**:\r\nMacOS Ventura 13.3.1 (a) (22E772610a)\r\nPrivate window\r\n\r\n**What seems to be the trouble?(Required)**\r\n- [ ] Desktop site instead of mobile site\r\n- [ ] Mobile site is not usable\r\n- [ ] Video doesn\'t play\r\n- [X] Layout is messed up\r\n- [X] Text is not visible\r\n- [ ] Something else (Add details below)\r\n\r\n**Steps to Reproduce**\r\n\r\n1. Navigate to: (www.samarabags.com)\r\n2. Select a product and open its page.\r\n\r\n*__Expected Behavior:__*\r\nThe customer review, Instagram and the footer are visible.\r\n\r\n*__Actual Behavior:__*\r\nAnything below the product\'s image is just blank. "This page slowing down Firefox" message appears on the top.\r\n\r\n**Screenshot**\r\n<img width="1510" alt="Screenshot 2023-05-12 at 6 24 29 PM" src="https://github.com/webcompat/web-bugs/assets/1740517/20423943-c0a2-42b4-a763-ff814fa48ecb">\r\n',
            '\n 1. Navigate to: (www.samarabags.com)\r\n2. Select a product and open its page.\r\n\r\n*__Expected Behavior:__*\r\nThe customer review, Instagram and the footer are visible.\r\n\r\n*__Actual Behavior:__*\r\nAnything below the product\'s image is just blank. "This page slowing down Firefox" message appears on the top.',
        ),
        (
            '<!-- @browser: Firefox Nightly 108.0a1 (2022-10-18) -->\r\n<!-- @ua_header: Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:108.0) Gecko/20100101 Firefox/108.0 -->\r\n<!-- @reported_with: unknown -->\r\n\r\n**URL**: https://dlive.tv/s/dashboard#0\r\n\r\n**Browser / Version**: Firefox Nightly 108.0a1 (2022-10-18)\r\n**Operating System**: Windows 10\r\n**Tested Another Browser**: Yes Chrome\r\n\r\n**Problem type**: Design is broken\r\n**Description**: Items are misaligned\r\n\r\n**Prerequisites**: \r\nAccount created and signed in.\r\n\r\n**Steps to Reproduce**:\r\n1. Navigate to https://dlive.tv/s/dashboard#0\r\n2. Type in a message in the "Chat". \r\n3. Observe text alignment. \r\n\r\n**Expected Behavior:**\r\nThe text is centered in the message field.\r\n\r\n**Actual Behavior:**\r\nThe text is aligned on the top side of the message field.\r\n\r\n**Notes:**\r\n1. The issue is not reproducible on Chrome.\r\n2. The issue is also reproducible on Firefox Release.\r\n3. The issue is also reproducible for the hint text in the message field.\r\n3. Screenshot attached. \r\n\r\n**Watchers:**\r\n@softvision-oana-arbuzov\r\n@softvision-raul-bucata\r\n@sv-calin \r\n<details>\r\n      <summary>View the screenshot</summary>\r\n      <img alt="Screenshot" src="https://webcompat.com/uploads/2022/10/b4a296a5-ee2f-4a18-a5da-b1e20ee8d27d.jpg">\r\n      </details>\r\n\r\n<details>\r\n<summary>Browser Configuration</summary>\r\n<ul>\r\n  <li>None</li>\r\n</ul>\r\n</details>\r\n\r\n_From [webcompat.com](https://webcompat.com/) with ❤️_',
            '1. Navigate to https://dlive.tv/s/dashboard#0\r\n2. Type in a message in the "Chat". \r\n3. Observe text alignment. \r\n\r\n**Expected Behavior:**\r\nThe text is centered in the message field.\r\n\r\n**Actual Behavior:**\r\nThe text is aligned on the top side of the message field.\r\n\r\n**Notes:**\r\n1. The issue is not reproducible on Chrome.\r\n2. The issue is also reproducible on Firefox Release.\r\n3. The issue is also reproducible for the hint text in the message field.\r\n3. Screenshot attached.',
        ),
    ]
    for orig_text, cleaned_text in tests:
        assert (
            feature_cleanup.CleanCompatibilityReportDescription()(orig_text)
            == cleaned_text
        )
