# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import json
import os

import pytest

from bugbug.bug_features import (
    blocked_bugs_number,
    bug_reporter,
    comment_count,
    comment_length,
    component,
    has_crash_signature,
    has_cve_in_alias,
    has_github_url,
    has_regression_range,
    has_str,
    has_url,
    has_w3c_url,
    is_coverity_issue,
    is_mozillian,
    keywords,
    landings,
    patches,
    product,
    severity,
    whiteboard,
)


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
    read("has_str.json", has_str, ["yes", None, "no"])


def test_has_regression_range(read):
    read("has_regression_range.json", has_regression_range, ["yes", None])


def test_has_crash_signature(read):
    read("has_crash_signature.json", has_crash_signature, [False, True])


def test_keywords(read):
    read(
        "keywords.json",
        keywords,
        [
            ["crash", "intermittent-failure", "stale-bug"],
            ["bulk-close-intermittents", "crash", "intermittent-failure"],
        ],
    )


def test_severity(read):
    read("severity.json", severity, ["major", "normal"])


def test_is_coverity_issue(read):
    read("is_coverity_issue.json", is_coverity_issue, [False, True])


def test_has_url(read):
    read("has_url.json", has_url, [True, False])


def test_has_w3c_url(read):
    read("has_w3c_url.json", has_w3c_url, [True, False])


def test_has_github_url(read):
    read("has_github_url.json", has_github_url, [True, False])


def test_whiteboard(read):
    read(
        "whiteboard.json",
        whiteboard,
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
    read("patches.json", patches, [1, 0])


def test_landings(read):
    read("landings.json", landings, [2, 1])


def test_product(read):
    read("product.json", product, ["Core", "Firefox for Android"])


def test_component(read):
    read("component.json", component, ["Graphics", "CSS Parsing and Computation"])


def test_is_mozillian(read):
    read("is_mozillian.json", is_mozillian, [False, True, True])


def test_blocked_bugs_number(read):
    read("blocked_bugs_number.json", blocked_bugs_number, [2, 0])


def test_bug_reporter(read):
    read(
        "bug_reporter.json",
        bug_reporter,
        [
            "bill.mccloskey@gmail.com",
            "rhelmer@mozilla.com",
            "intermittent-bug-filer@mozilla.bugs",
        ],
    )


def test_has_cve_in_alias(read):
    read("has_cve_in_alias.json", has_cve_in_alias, [True, False])


def test_comment_count(read):
    read("comment_count.json", comment_count, [4, 28])


def test_comment_length(read):
    read("comment_length.json", comment_length, [566, 5291])
