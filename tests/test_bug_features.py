# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import json
import os

from bugbug.bug_features import blocked_bugs_number
from bugbug.bug_features import bug_reporter
from bugbug.bug_features import comment_count
from bugbug.bug_features import comment_length
from bugbug.bug_features import component
from bugbug.bug_features import has_crash_signature
from bugbug.bug_features import has_cve_in_alias
from bugbug.bug_features import has_github_url
from bugbug.bug_features import has_regression_range
from bugbug.bug_features import has_str
from bugbug.bug_features import has_url
from bugbug.bug_features import has_w3c_url
from bugbug.bug_features import is_coverity_issue
from bugbug.bug_features import is_mozillian
from bugbug.bug_features import keywords
from bugbug.bug_features import landings
from bugbug.bug_features import patches
from bugbug.bug_features import product
from bugbug.bug_features import severity
from bugbug.bug_features import title
from bugbug.bug_features import whiteboard


def read(filename, feature_extractor_class, expected_results):
    path = os.path.join('tests/fixtures/bug_features', filename)
    feature_extractor = feature_extractor_class()
    assert os.path.exists(path)

    with open(path, 'r') as f:
        results = (feature_extractor(json.loads(line)) for line in f)
        for result, expected_result in zip(results, expected_results):
            assert result == expected_result


def test_has_str():
    read('has_str.json', has_str, ['yes', None, 'no'])


def test_has_regression_range():
    read('has_regression_range.json', has_regression_range, ['yes', None])


def test_has_crash_signature():
    read('has_crash_signature.json', has_crash_signature, [False, True])


def test_keywords():
    read('keywords.json', keywords, [['crash', 'intermittent-failure', 'stale-bug'], ['bulk-close-intermittents', 'crash', 'intermittent-failure']])


def test_severity():
    read('severity.json', severity, ['major', 'normal'])


def test_is_coverity_issue():
    read('is_coverity_issue.json', is_coverity_issue, [False, True])


def test_has_url():
    read('has_url.json', has_url, [True, False])


def test_has_w3c_url():
    read('has_w3c_url.json', has_w3c_url, [True, False])


def test_has_github_url():
    read('has_github_url.json', has_github_url, [True, False])


def test_whiteboard():
    read('whiteboard.json', whiteboard, [['memshrink', 'platform-rel-facebook'], [], ['inj+', 'av:quick heal', 'av'], ['av:quick heal', 'regressed sept 6th', 'dll version is 3.0.1.*', 'av'], ['av:quick heal', 'inj+', 'av'], ['av:quick heal', 'inj+', 'av'], ['inj+', 'av:quick heal', 'av'], ['inj+', 'av:quick heal', 'av'], ['inj+', 'ux', 'av:quick heal', 'qf', 'av']])


def test_patches():
    read('patches.json', patches, [1, 0])


def test_landings():
    read('landings.json', landings, [2, 1])


def test_title():
    read('title.json', title, [['fail'], []])


def test_product():
    read('product.json', product, ['Core', 'Firefox for Android'])


def test_component():
    read('component.json', component, ['Graphics', 'CSS Parsing and Computation'])


def test_is_mozillian():
    read('is_mozillian.json', is_mozillian, [False, True, True])


def test_blocked_bugs_number():
    read('blocked_bugs_number.json', blocked_bugs_number, [2, 0])


def test_bug_reporter():
    read('bug_reporter.json', bug_reporter, ['bill.mccloskey@gmail.com', 'rhelmer@mozilla.com', 'intermittent-bug-filer@mozilla.bugs'])


def test_has_cve_in_alias():
    read('has_cve_in_alias.json', has_cve_in_alias, [True, False])


def test_comment_count():
    read('comment_count.json', comment_count, [4, 28])


def test_comment_length():
    read('comment_length.json', comment_length, [566, 5291])
