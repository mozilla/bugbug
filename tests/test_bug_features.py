# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import json
import os

import pytest

from bugbug import bug_features
from bugbug.bug_features import (
    BlockedBugsNumber,
    BugExtractor,
    BugReporter,
    BugTypes,
    CommentCount,
    CommentFirstPathComponent,
    CommentLength,
    CommentPathsComponents,
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
    IsMozillian,
    Keywords,
    Landings,
    Patches,
    Product,
    Severity,
    Whiteboard,
    extract_path_components,
    find_component,
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


@pytest.fixture
def mock_component_mapping(monkeypatch):
    # Mimics repository.get_component_mapping(), which is backed by an
    # LMDBDict with bytes keys and memoryview values.
    mapping = {
        b"dom/base/nsGlobalWindow.cpp": memoryview(b"Core::DOM"),
        b"layout/generic/nsFrame.cpp": memoryview(b"Core::Layout"),
    }
    monkeypatch.setattr(
        bug_features.repository, "get_component_mapping", lambda: mapping
    )
    return mapping


def test_extract_path_components(mock_component_mapping):
    comments = [
        {"text": "The crash happens in dom/base/nsGlobalWindow.cpp around line 42."},
        {
            "text": "It might also be related to layout/generic/nsFrame.cpp and "
            "some/unknown/path.cpp."
        },
    ]

    assert extract_path_components(comments) == ["Core::DOM", "Core::Layout"]


def test_extract_path_components_no_match(mock_component_mapping):
    comments = [{"text": "Nothing looks like a source path here."}]

    assert extract_path_components(comments) == []


def test_find_component_exact_match(mock_component_mapping):
    assert (
        find_component(b"dom/base/nsGlobalWindow.cpp", mock_component_mapping)
        == "Core::DOM"
    )


def test_find_component_longest_suffix_match(mock_component_mapping):
    # The path has an extra leading directory (e.g. a local checkout root),
    # so it doesn't match a mapping key exactly, but its longest suffix does.
    assert (
        find_component(
            b"mozilla-central/dom/base/nsGlobalWindow.cpp", mock_component_mapping
        )
        == "Core::DOM"
    )


def test_find_component_no_match(mock_component_mapping):
    assert find_component(b"some/unknown/path.cpp", mock_component_mapping) is None


def test_extract_path_components_extra_prefix(mock_component_mapping):
    comments = [
        {"text": "Reproduced with src/mozilla-central/dom/base/nsGlobalWindow.cpp."}
    ]

    assert extract_path_components(comments) == ["Core::DOM"]


def test_comment_first_path_component(mock_component_mapping):
    bug = {
        "comments": [
            {"text": "See layout/generic/nsFrame.cpp and dom/base/nsGlobalWindow.cpp."}
        ]
    }

    assert CommentFirstPathComponent()(bug) == "Core::Layout"


def test_comment_first_path_component_no_match(mock_component_mapping):
    bug = {"comments": [{"text": "Nothing looks like a source path here."}]}

    assert CommentFirstPathComponent()(bug) is None


def test_comment_paths_components(mock_component_mapping):
    bug = {
        "comments": [
            {"text": "See layout/generic/nsFrame.cpp and dom/base/nsGlobalWindow.cpp."}
        ]
    }

    assert CommentPathsComponents()(bug) == {"Core::Layout": 1, "Core::DOM": 1 / 2}


def test_comment_paths_components_repeated_mentions(mock_component_mapping):
    bug = {
        "comments": [
            {
                "text": "See layout/generic/nsFrame.cpp, then "
                "dom/base/nsGlobalWindow.cpp, then layout/generic/nsFrame.cpp again."
            }
        ]
    }

    assert CommentPathsComponents()(bug) == {
        "Core::Layout": 1 + 1 / 3,
        "Core::DOM": 1 / 2,
    }
