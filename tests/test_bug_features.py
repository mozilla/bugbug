# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import json
import os

import pytest

from bugbug import bugzilla
from bugbug.bug_features import (
    BlockedBugsNumber,
    BugExtractor,
    BugReporter,
    CommentCount,
    CommentLength,
    Component,
    DeltaNightlyRequestMerge,
    HasCrashSignature,
    HasCVEInAlias,
    HasGithubURL,
    HasRegressionRange,
    HasSTR,
    HasURL,
    HasW3CURL,
    IsCoverityIssue,
    IsCrashBug,
    IsMemoryBug,
    IsMozillian,
    IsPerformanceBug,
    IsPowerBug,
    IsSecurityBug,
    Keywords,
    Landings,
    Patches,
    Product,
    Severity,
    Whiteboard,
    infer_bug_types,
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


def test_is_performance_bug() -> None:
    is_performance_bug = IsPerformanceBug()

    bug_map = {int(bug["id"]): bug for bug in bugzilla.get_bugs(include_invalid=True)}

    assert is_performance_bug(bug_map[447581]) is True
    assert is_performance_bug(bug_map[1320195]) is True
    assert is_performance_bug(bug_map[1388990]) is False
    assert is_performance_bug(bug_map[1389136]) is False


def test_is_memory_bug() -> None:
    is_memory_bug = IsMemoryBug()

    bug_map = {int(bug["id"]): bug for bug in bugzilla.get_bugs(include_invalid=True)}

    assert is_memory_bug(bug_map[1325215], bug_map) is True
    assert is_memory_bug(bug_map[52352], bug_map) is True
    assert is_memory_bug(bug_map[1320195], bug_map) is False
    assert is_memory_bug(bug_map[1388990]) is False


def test_is_power_bug() -> None:
    is_power_bug = IsPowerBug()

    bug_map = {int(bug["id"]): bug for bug in bugzilla.get_bugs(include_invalid=True)}

    assert is_power_bug(bug_map[922874]) is True
    assert is_power_bug(bug_map[965392]) is True
    assert is_power_bug(bug_map[1325215]) is False
    assert is_power_bug(bug_map[1320195]) is False


def test_is_security_bug() -> None:
    is_security_bug = IsSecurityBug()

    bug_map = {int(bug["id"]): bug for bug in bugzilla.get_bugs(include_invalid=True)}

    assert is_security_bug(bug_map[528988]) is True
    assert is_security_bug(bug_map[1320039]) is True
    assert is_security_bug(bug_map[922874]) is False
    assert is_security_bug(bug_map[965392]) is False


def test_is_crash_bug() -> None:
    is_crash_bug = IsCrashBug()

    bug_map = {int(bug["id"]): bug for bug in bugzilla.get_bugs(include_invalid=True)}

    assert is_crash_bug(bug_map[1046231]) is True
    assert is_crash_bug(bug_map[1046231]) is True
    assert is_crash_bug(bug_map[528988]) is False
    assert is_crash_bug(bug_map[1320039]) is False


def test_infer_bug_types() -> None:
    bug_map = {int(bug["id"]): bug for bug in bugzilla.get_bugs(include_invalid=True)}

    result = infer_bug_types(bug_map[447581])
    assert isinstance(result, list)
    assert "performance" in result

    result = infer_bug_types(bug_map[1325215], bug_map)
    assert isinstance(result, list)
    assert "memory" in result

    result = infer_bug_types(bug_map[922874])
    assert isinstance(result, list)
    assert "power" in result

    result = infer_bug_types(bug_map[528988])
    assert isinstance(result, list)
    assert "security" in result

    result = infer_bug_types(bug_map[1046231])
    assert isinstance(result, list)
    assert "crash" in result
