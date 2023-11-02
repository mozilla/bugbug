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
    IsFirstAffectedSame,
    IsMozillian,
    IsSameComponent,
    IsSameOS,
    IsSamePlatform,
    IsSameProduct,
    IsSameTargetMilestone,
    IsSameVersion,
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


PRODUCT_PARAMS = [
    ([{"product": "Firefox"}, {"product": "Firefox"}], True),
    ([{"product": "Firefox"}, {"product": "Firefox for Android"}], False),
]


@pytest.mark.parametrize("test_data, expected", PRODUCT_PARAMS)
def test_is_same_product(test_data, expected):
    assert IsSameProduct()(test_data) == expected


COMPONENT_PARAMS = [
    (
        [
            {"product": "Firefox", "component": "Graphics"},
            {"product": "Firefox", "component": "Graphics"},
        ],
        True,
    ),
    (
        [
            {"product": "Firefox", "component": "Graphics"},
            {"product": "Core", "component": "Graphics"},
        ],
        False,
    ),
    (
        [
            {"product": "Firefox", "component": "Graphics"},
            {"product": "Firefox", "component": "General"},
        ],
        False,
    ),
    (
        [
            {"product": "Firefox", "component": "Graphics"},
            {"product": "Core", "component": "General"},
        ],
        False,
    ),
]


@pytest.mark.parametrize("test_data, expected", COMPONENT_PARAMS)
def test_is_same_component(test_data, expected):
    assert IsSameComponent()(test_data) == expected


PLATFORM_PARAMS = [
    ([{"platform": "Unspecified"}, {"platform": "Unspecified"}], True),
    ([{"platform": "All"}, {"platform": "x86_64"}], False),
]


@pytest.mark.parametrize("test_data, expected", PLATFORM_PARAMS)
def test_is_same_platform(test_data, expected):
    assert IsSamePlatform()(test_data) == expected


VERSION_PARAMS = [
    ([{"version": "55 Branch"}, {"version": "55 Branch"}], True),
    ([{"version": "Trunk"}, {"version": "unspecified"}], False),
]


@pytest.mark.parametrize("test_data, expected", VERSION_PARAMS)
def test_is_same_version(test_data, expected):
    assert IsSameVersion()(test_data) == expected


OS_PARAMS = [
    ([{"op_sys": "Unspecified"}, {"op_sys": "Unspecified"}], True),
    ([{"op_sys": "All"}, {"op_sys": "Unspecified"}], False),
]


@pytest.mark.parametrize("test_data, expected", OS_PARAMS)
def test_is_same_os(test_data, expected):
    assert IsSameOS()(test_data) == expected


TARGET_MILESTONE_PARAMS = [
    ([{"target_milestone": "Firefox 57"}, {"target_milestone": "Firefox 57"}], True),
    ([{"target_milestone": "mozilla57"}, {"target_milestone": "---"}], False),
]


@pytest.mark.parametrize("test_data, expected", TARGET_MILESTONE_PARAMS)
def test_is_same_target_milestone(test_data, expected):
    assert IsSameTargetMilestone()(test_data) == expected


FIRST_AFFECTED_PARAMS = [
    ([{"cf_status_firefox55": "affected"}, {"cf_status_firefox55": "affected"}], True),
    ([{"cf_status_firefox61": "unaffected"}, {"cf_status_firefox63": "fixed"}], False),
    ([{"cf_status_geckoview66": "verified"}, {"cf_status_geckoview66": "---"}], False),
    ([{"cf_status_firefox68": "---"}, {"cf_status_firefox68": "---"}], False),
]


@pytest.mark.parametrize("test_data, expected", FIRST_AFFECTED_PARAMS)
def test_is_first_affected_same(test_data, expected):
    assert IsFirstAffectedSame()(test_data) == expected


def test_BugExtractor():
    BugExtractor([HasSTR(), HasURL()], [fileref(), url()])
    with pytest.raises(AssertionError):
        BugExtractor([HasSTR(), HasSTR()], [fileref(), url()])
    with pytest.raises(AssertionError):
        BugExtractor([HasSTR(), HasURL()], [fileref(), fileref()])
