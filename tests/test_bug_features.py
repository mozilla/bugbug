# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import json
import os

import pytest

from bugbug.bug_features import (
    BlockedBugsNumber,
    BugExtractor,
    BugReporter,
    BugTypes,
    CommentCount,
    CommentLength,
    Component,
    DeltaNightlyRequestMerge,
    FilePaths,
    HasCrashSignature,
    HasCVEInAlias,
    HasGithubURL,
    HasRegressionRange,
    HasSTR,
    HasURL,
    HasW3CURL,
    IsCoverityIssue,
    IsMozillian,
    Keywords,
    Landings,
    Patches,
    Product,
    Severity,
    Whiteboard,
)
from bugbug.feature_cleanup import fileref, url


@pytest.fixture
def read(get_fixture_path):
    def _read(path, feature_extractor_class, expected_results):
        feature_extractor = feature_extractor_class()

        path = get_fixture_path(os.path.join("bug_features", path))

        with open(path, "r") as f:
            results = (feature_extractor(json.loads(line)) for line in f)
            for result, expected_result in zip(results, expected_results):
                assert result == expected_result

        return _read


def test_has_str(read):
    read("has_str.json", HasSTR, ["yes", None, "no"])


def test_has_regression_range(read):
    read("has_regression_range.json", HasRegressionRange, ["yes", None])


def test_has_crash_signature(read):
    read("has_crash_signature.json", HasCrashSignature, [False, True])


def test_keywords(read):
    read(
        "keywords.json",
        Keywords,
        [
            ["crash", "intermittent-failure", "stale-bug"],
            ["bulk-close-intermittents", "crash", "intermittent-failure"],
        ],
    )


def test_severity(read):
    read("severity.json", Severity, ["major", "normal"])


def test_is_coverity_issue(read):
    read("is_coverity_issue.json", IsCoverityIssue, [False, True])


def test_has_url(read):
    read("has_url.json", HasURL, [True, False])


def test_has_w3c_url(read):
    read("has_w3c_url.json", HasW3CURL, [True, False])


def test_has_github_url(read):
    read("has_github_url.json", HasGithubURL, [True, False])


def test_whiteboard(read):
    read(
        "whiteboard.json",
        Whiteboard,
        [
            ["memshrink", "platform-rel-facebook"],
            [],
            ["inj+", "av:quick heal", "av"],
            ["av:quick heal", "regressed sept 6th", "dll version is 3.0.1.*", "av"],
            ["av:quick heal", "inj+", "av"],
            ["av:quick heal", "inj+", "av"],
            ["inj+", "av:quick heal", "av"],
            ["inj+", "av:quick heal", "av"],
            ["inj+", "ux", "av:quick heal", "qf", "av"],
        ],
    )


def test_patches(read):
    read("patches.json", Patches, [1, 0])


def test_landings(read):
    read("landings.json", Landings, [2, 1])


def test_product(read):
    read("product.json", Product, ["Core", "Firefox for Android"])


def test_component(read):
    read("component.json", Component, ["Graphics", "CSS Parsing and Computation"])


def test_is_mozillian(read):
    read("is_mozillian.json", IsMozillian, [False, True, True])


def test_blocked_bugs_number(read):
    read("blocked_bugs_number.json", BlockedBugsNumber, [2, 0])


def test_bug_reporter(read):
    read(
        "bug_reporter.json",
        BugReporter,
        [
            "bill.mccloskey@gmail.com",
            "rhelmer@mozilla.com",
            "intermittent-bug-filer@mozilla.bugs",
        ],
    )


def test_has_cve_in_alias(read):
    read("has_cve_in_alias.json", HasCVEInAlias, [True, False])


def test_comment_count(read):
    read("comment_count.json", CommentCount, [4, 28])


def test_comment_length(read):
    read("comment_length.json", CommentLength, [566, 5291])


def test_delta_nightly_request_merge(read):
    read(
        "nightly_uplift.json",
        DeltaNightlyRequestMerge,
        [
            pytest.approx(6.431805555555556),
            pytest.approx(0.8732638888888888),
            None,
            None,
        ],
    )


def test_BugExtractor():
    BugExtractor([HasSTR(), HasURL()], [fileref(), url()])
    with pytest.raises(AssertionError):
        BugExtractor([HasSTR(), HasSTR()], [fileref(), url()])
    with pytest.raises(AssertionError):
        BugExtractor([HasSTR(), HasURL()], [fileref(), fileref()])


def test_BugTypes(read) -> None:
    read(
        "bug_types.json",
        BugTypes,
        [["performance"], ["memory"], ["power"], ["security"], ["crash"]],
    )


def test_FilePaths(read):
    inline_data = [
        {
            "summary": "<nsFrame.cpp> cleanup",
            "comments": [
                {
                    "text": "Fix for\n{{ <http://tinderbox.mozilla.org/SeaMonkey/warn1082809200.7591.html>\nanthonyd (2 warnings)\n1.\tlayout/html/base/src/nsFrame.cpp:3879 (See build log excerpt)\n\t`nsIFrame*thisBlock' might be used uninitialized in this function\n2.\tlayout/html/base/src/nsFrame.cpp:3908 (See build log excerpt)\n\t`nsIFrame*thisBlock' might be used uninitialized in this function\n}} (NB: lines should be lines - 2, due to checkin \"in progress\")\nwill be included."
                }
            ],
        },
        {
            "summary": "Spidermonkey regression causes treehydra trunk to fail 6 tests",
            "comments": [
                {
                    "text": 'Today I\'m trying to get callgraph stuff hooked into dxr, and I\'m unable to get a working treehydra.  I\'ve updated tried updating just dehydra, then I updated gcc w/plugins using the new stuff in the patch queue, and it doesn\'t matter.  Running make check_treehydra fails like this:\n\nTest Failure: \n    Test command: /var/www/html/dxr/tools/gcc-dehydra/installed/bin/../libexec/gcc/x86_64-unknown-linux-gnu/4.3.0/cc1plus -quiet -fplugin=../gcc_treehydra.so -o /dev/null -fplugin-arg=test_locks_bad3.js locks_bad3.cc\n    Failure msg: Expected \'locks_bad3.cc:10: error: precondition not met\' in error output; not found. stderr:../libs/treehydra.js:12: JS Exception: No case_val in this lazy object\n:0:     #0: Error("No case_val in this lazy object")\n../libs/treehydra.js:12:        #1: unhandledLazyProperty("case_val")\n../libs/unstable/esp.js:481:    #2: ()\n./esp_lock.js:41:       #3: process_tree([object GCCNode])\n\nTest Failure: \n    Test command: /var/www/html/dxr/tools/gcc-dehydra/installed/bin/../libexec/gcc/x86_64-unknown-linux-gnu/4.3.0/cc1plus -quiet -fplugin=../gcc_treehydra.so -o /dev/null -fplugin-arg=test_locks_good.js locks_good.cc\n    Failure msg: Expected no error output, got error output :../libs/treehydra.js:12: JS Exception: No case_val in this lazy object\n:0:     #0: Error("No case_val in this lazy object")\n../libs/treehydra.js:12:        #1: unhandledLazyProperty("case_val")\n../libs/unstable/esp.js:481:    #2: ()\n./esp_lock.js:41:       #3: process_tree([object GCCNode])\n\nTest Failure: \n    Test command: /var/www/html/dxr/tools/gcc-dehydra/installed/bin/../libexec/gcc/x86_64-unknown-linux-gnu/4.3.0/cc1plus -quiet -fplugin=../gcc_treehydra.so -o /dev/null -fplugin-arg=test_locks_good2.js locks_good2.cc\n    Failure msg: Expected no error output, got error output :../libs/treehydra.js:12: JS Exception: No case_val in this lazy object\n:0:     #0: Error("No case_val in this lazy object")\n../libs/treehydra.js:12:        #1: unhandledLazyProperty("case_val")\n../libs/unstable/esp.js:481:    #2: ()\n./esp_lock.js:41:       #3: process_tree([object GCCNode])\n\nTest Failure: \n    Test command: /var/www/html/dxr/tools/gcc-dehydra/installed/bin/../libexec/gcc/x86_64-unknown-linux-gnu/4.3.0/cc1plus -quiet -fplugin=../gcc_treehydra.so -o /dev/null -fplugin-arg=test_locks_bad4.js locks_bad4.cc\n    Failure msg: Expected \'locks_bad4.cc:13: error: precondition not met\' in error output; not found. stderr:../libs/treehydra.js:12: JS Exception: No case_val in this lazy object\n:0:     #0: Error("No case_val in this lazy object")\n../libs/treehydra.js:12:        #1: unhandledLazyProperty("case_val")\n../libs/unstable/esp.js:481:    #2: ()\n./esp_lock.js:41:       #3: process_tree([object GCCNode])\n\nTest Failure: \n    Test command: /var/www/html/dxr/tools/gcc-dehydra/installed/bin/../libexec/gcc/x86_64-unknown-linux-gnu/4.3.0/cc1plus -quiet -fplugin=../gcc_treehydra.so -o /dev/null -fplugin-arg=test_locks_bad2.js locks_bad2.cc\n    Failure msg: Expected \'locks_bad2.cc:12: error: precondition not met\' in error output; not found. stderr:../libs/treehydra.js:12: JS Exception: No case_val in this lazy object\n:0:     #0: Error("No case_val in this lazy object")\n../libs/treehydra.js:12:        #1: unhandledLazyProperty("case_val")\n../libs/unstable/esp.js:481:    #2: ()\n./esp_lock.js:41:       #3: process_tree([object GCCNode])\n\nTest Failure: \n    Test command: /var/www/html/dxr/tools/gcc-dehydra/installed/bin/../libexec/gcc/x86_64-unknown-linux-gnu/4.3.0/cc1plus -quiet -fplugin=../gcc_treehydra.so -o /dev/null -fplugin-arg=test_locks_bad1.js locks_bad1.cc\n    Failure msg: Expected \'locks_bad1.cc:11: error: precondition not met\' in error output; not found. stderr:../libs/treehydra.js:12: JS Exception: No case_val in this lazy object\n:0:     #0: Error("No case_val in this lazy object")\n../libs/treehydra.js:12:        #1: unhandledLazyProperty("case_val")\n../libs/unstable/esp.js:481:    #2: ()\n./esp_lock.js:41:       #3: process_tree([object GCCNode])\n\n\nUnit Test Suite Summary:\n     32 passed\n      6 failed\n      0 error(s)\nmake[1]: *** [check_treehydra] Error 1\nmake[1]: Leaving directory `/var/www/html/dxr/tools/gcc-dehydra/dehydra/test\'\nmake: *** [check] Error 2'
                }
            ],
        },
    ]
    expected_results = [
        [
            "nsFrame.cpp",
            "layout",
            "html",
            "base",
            "src",
            "nsFrame.cpp",
            "layout/html/base/src/nsFrame.cpp",
            "html/base/src/nsFrame.cpp",
            "base/src/nsFrame.cpp",
            "src/nsFrame.cpp",
            "nsFrame.cpp",
            "layout",
            "html",
            "base",
            "src",
            "nsFrame.cpp",
            "layout/html/base/src/nsFrame.cpp",
            "html/base/src/nsFrame.cpp",
            "base/src/nsFrame.cpp",
            "src/nsFrame.cpp",
            "nsFrame.cpp",
        ],
        [
            "gcc_treehydra.so",
            "/gcc_treehydra.so",
            "gcc_treehydra.so",
            "test_locks_bad3.js",
            "locks_bad3.cc",
            "locks_bad3.cc",
            "libs",
            "treehydra.js",
            "/libs/treehydra.js",
            "libs/treehydra.js",
            "treehydra.js",
            "libs",
            "treehydra.js",
            "/libs/treehydra.js",
            "libs/treehydra.js",
            "treehydra.js",
            "libs",
            "unstable",
            "esp.js",
            "/libs/unstable/esp.js",
            "libs/unstable/esp.js",
            "unstable/esp.js",
            "esp.js",
            "esp_lock.js",
            "/esp_lock.js",
            "esp_lock.js",
            "gcc_treehydra.so",
            "/gcc_treehydra.so",
            "gcc_treehydra.so",
            "test_locks_good.js",
            "locks_good.cc",
            "libs",
            "treehydra.js",
            "/libs/treehydra.js",
            "libs/treehydra.js",
            "treehydra.js",
            "libs",
            "treehydra.js",
            "/libs/treehydra.js",
            "libs/treehydra.js",
            "treehydra.js",
            "libs",
            "unstable",
            "esp.js",
            "/libs/unstable/esp.js",
            "libs/unstable/esp.js",
            "unstable/esp.js",
            "esp.js",
            "esp_lock.js",
            "/esp_lock.js",
            "esp_lock.js",
            "gcc_treehydra.so",
            "/gcc_treehydra.so",
            "gcc_treehydra.so",
            "test_locks_good2.js",
            "locks_good2.cc",
            "libs",
            "treehydra.js",
            "/libs/treehydra.js",
            "libs/treehydra.js",
            "treehydra.js",
            "libs",
            "treehydra.js",
            "/libs/treehydra.js",
            "libs/treehydra.js",
            "treehydra.js",
            "libs",
            "unstable",
            "esp.js",
            "/libs/unstable/esp.js",
            "libs/unstable/esp.js",
            "unstable/esp.js",
            "esp.js",
            "esp_lock.js",
            "/esp_lock.js",
            "esp_lock.js",
            "gcc_treehydra.so",
            "/gcc_treehydra.so",
            "gcc_treehydra.so",
            "test_locks_bad4.js",
            "locks_bad4.cc",
            "locks_bad4.cc",
            "libs",
            "treehydra.js",
            "/libs/treehydra.js",
            "libs/treehydra.js",
            "treehydra.js",
            "libs",
            "treehydra.js",
            "/libs/treehydra.js",
            "libs/treehydra.js",
            "treehydra.js",
            "libs",
            "unstable",
            "esp.js",
            "/libs/unstable/esp.js",
            "libs/unstable/esp.js",
            "unstable/esp.js",
            "esp.js",
            "esp_lock.js",
            "/esp_lock.js",
            "esp_lock.js",
            "gcc_treehydra.so",
            "/gcc_treehydra.so",
            "gcc_treehydra.so",
            "test_locks_bad2.js",
            "locks_bad2.cc",
            "locks_bad2.cc",
            "libs",
            "treehydra.js",
            "/libs/treehydra.js",
            "libs/treehydra.js",
            "treehydra.js",
            "libs",
            "treehydra.js",
            "/libs/treehydra.js",
            "libs/treehydra.js",
            "treehydra.js",
            "libs",
            "unstable",
            "esp.js",
            "/libs/unstable/esp.js",
            "libs/unstable/esp.js",
            "unstable/esp.js",
            "esp.js",
            "esp_lock.js",
            "/esp_lock.js",
            "esp_lock.js",
            "gcc_treehydra.so",
            "/gcc_treehydra.so",
            "gcc_treehydra.so",
            "test_locks_bad1.js",
            "locks_bad1.cc",
            "locks_bad1.cc",
            "libs",
            "treehydra.js",
            "/libs/treehydra.js",
            "libs/treehydra.js",
            "treehydra.js",
            "libs",
            "treehydra.js",
            "/libs/treehydra.js",
            "libs/treehydra.js",
            "treehydra.js",
            "libs",
            "unstable",
            "esp.js",
            "/libs/unstable/esp.js",
            "libs/unstable/esp.js",
            "unstable/esp.js",
            "esp.js",
            "esp_lock.js",
            "/esp_lock.js",
            "esp_lock.js",
        ],
    ]

    results = (FilePaths(item) for item in inline_data)

    for result, expected_result in zip(results, expected_results):
        assert result == expected_result
