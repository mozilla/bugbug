# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import re

from sklearn.base import BaseEstimator
from sklearn.base import TransformerMixin


def field(bug, field):
    if field in bug and bug[field] != '---':
        return bug[field]

    return None


def has_str(bug):
    return field(bug, 'cf_has_str')


def has_regression_range(bug):
    return field(bug, 'cf_has_regression_range')


def has_crash_signature(bug):
    return 'cf_crash_signature' in bug and bug['cf_crash_signature'] != ''


def keywords(bug):
    keywords = []
    subkeywords = []
    for keyword in bug['keywords']:
        # Ignore keywords that would make the ML completely skewed (we are going to use them as 100% rules in the evaluation phase).
        if keyword in ['regression', 'talos-regression', 'feature']:
            continue

        keywords.append(keyword)

        if keyword.startswith('sec-'):
            subkeywords.append('sec-')
        elif keyword.startswith('csectype-'):
            subkeywords.append('csectype-')
    return keywords + subkeywords


def severity(bug):
    return field(bug, 'severity')


def is_coverity_issue(bug):
    return re.search('[CID ?[0-9]+]', bug['summary']) is not None or re.search('[CID ?[0-9]+]', bug['whiteboard']) is not None


def has_url(bug):
    return bug['url'] != ''


def has_w3c_url(bug):
    return 'w3c' in bug['url']


def has_github_url(bug):
    return 'github' in bug['url']


def whiteboard(bug):
    ret = []

    # TODO: Add any [XXX:YYY] that appears in the whiteboard as [XXX: only

    for elem in ['memshrink', '[ux]']:
        if elem in bug['whiteboard'].lower():
            ret.append(elem)

    return ret


def patches(bug):
    return sum(1 for a in bug['attachments'] if a['is_patch'] or a['content_type'] == 'text/x-review-board-request')


def landings(bug):
    return sum(1 for c in bug['comments'] if '://hg.mozilla.org/' in c['text'])


def title(bug):
    ret = []

    keywords = [
        'implement', 'refactor', 'meta', 'tracker', 'dexpcom',
        'indent', 'ui review', 'support', '[ux]',
        'fail', 'npe', 'except', 'broken', 'crash', 'bug', 'differential testing', 'error',
        'addresssanitizer', 'hang ', ' hang', 'jsbugmon', 'leak', 'permaorange', 'random orange',
        'intermittent', 'regression', 'test fix', 'heap overflow', 'uaf', 'use-after-free',
        'asan', 'address sanitizer', 'rooting hazard', 'race condition', 'xss', '[static analysis]',
        'warning c',
    ]
    for keyword in keywords:
        if keyword in bug['summary'].lower():
            ret.append(keyword)

    keyword_couples = [
        ('add', 'test')
    ]
    for keyword1, keyword2 in keyword_couples:
        if keyword1 in bug['summary'].lower() and keyword2 in bug['summary'].lower():
            ret.append(keyword1 + '^' + keyword2)

    return ret


def comments(bug):
    ret = set()

    keywords = [
        'refactor',
        'steps to reproduce', 'crash', 'assertion', 'failure', 'leak', 'stack trace', 'regression',
        'test fix', ' hang', 'hang ', 'heap overflow', 'str:', 'use-after-free', 'asan',
        'address sanitizer', 'permafail', 'intermittent', 'race condition', 'unexpected fail',
        'unexpected-fail', 'unexpected pass', 'unexpected-pass', 'repro steps:', 'to reproduce:',
    ]

    casesensitive_keywords = [
        'FAIL', 'UAF',
    ]

    for keyword in keywords:
        if keyword in bug['comments'][0]['text'].lower():
            ret.add('first^' + keyword)

    for keyword in casesensitive_keywords:
        if keyword in bug['comments'][0]['text']:
            ret.add('first^' + keyword)

    mozregression_patterns = [
        'mozregression', 'Looks like the following bug has the changes which introduced the regression', 'First bad revision',
    ]

    for keyword in mozregression_patterns:
        for comment in bug['comments']:
            if keyword in comment['text'].lower():
                ret.add('mozregression')

    safemode_patterns = [
        'safemode', 'safe mode'
    ]

    for keyword in safemode_patterns:
        for comment in bug['comments']:
            if keyword in comment['text'].lower():
                ret.add('safemode')

    return list(ret)


feature_extractors = [
    has_str,
    # Ignore features that would make the ML completely skewed (we are going to use them as 100% rules in the evaluation phase).
    # has_regression_range,
    severity,
    keywords,
    is_coverity_issue,
    has_crash_signature,
    has_url,
    has_w3c_url,
    has_github_url,
    whiteboard,
    patches,
    landings,
    title,
    comments,
]


class BugExtractor(BaseEstimator, TransformerMixin):
    def __init__(self, commit_messages_map=None):
        self.commit_messages_map = commit_messages_map

    def fit(self, x, y=None):
        return self

    def transform(self, bugs):
        results = []

        for bug in bugs:
            bug_id = bug['id']

            data = {}

            for f in feature_extractors:
                res = f(bug)

                if res is None:
                    continue

                if isinstance(res, list):
                    for item in res:
                        data[f.__name__ + '-' + item] = 'True'
                    continue

                if isinstance(res, bool):
                    res = str(res)

                data[f.__name__] = res

            # TODO: Try simply using all possible fields instead of extracting features manually.

            result = {
                'data': data,
                'title': bug['summary'],
                'comments': ' '.join([c['text'] for c in bug['comments']]),
            }

            if self.commit_messages_map is not None:
                result['commits'] = self.commit_messages_map[bug_id] if bug_id in self.commit_messages_map else ''

            results.append(result)

        return results
