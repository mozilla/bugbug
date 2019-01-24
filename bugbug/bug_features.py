# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import re

import pandas as pd
from sklearn.base import BaseEstimator
from sklearn.base import TransformerMixin

from bugbug import bug_snapshot
from bugbug import repository


def field(bug, field):
    if field in bug and bug[field] != '---':
        return bug[field]

    return None


class has_str(object):
    def __call__(self, bug):
        return field(bug, 'cf_has_str')


class has_regression_range(object):
    def __call__(self, bug):
        return field(bug, 'cf_has_regression_range')


class has_crash_signature(object):
    def __call__(self, bug):
        return 'cf_crash_signature' in bug and bug['cf_crash_signature'] != ''


class keywords(object):
    def __init__(self, to_ignore=set()):
        self.to_ignore = to_ignore

    def __call__(self, bug):
        keywords = []
        subkeywords = []
        for keyword in bug['keywords']:
            if keyword in self.to_ignore:
                continue

            keywords.append(keyword)

            if keyword.startswith('sec-'):
                subkeywords.append('sec-')
            elif keyword.startswith('csectype-'):
                subkeywords.append('csectype-')
        return keywords + subkeywords


class severity(object):
    def __call__(self, bug):
        return field(bug, 'severity')


class is_coverity_issue(object):
    def __call__(self, bug):
        return re.search('[CID ?[0-9]+]', bug['summary']) is not None or re.search('[CID ?[0-9]+]', bug['whiteboard']) is not None


class has_url(object):
    def __call__(self, bug):
        return bug['url'] != ''


class has_w3c_url(object):
    def __call__(self, bug):
        return 'w3c' in bug['url']


class has_github_url(object):
    def __call__(self, bug):
        return 'github' in bug['url']


class whiteboard(object):
    def __call__(self, bug):
        ret = []

        # TODO: Add any [XXX:YYY] that appears in the whiteboard as [XXX: only

        for elem in ['memshrink', '[ux]']:
            if elem in bug['whiteboard'].lower():
                ret.append(elem)

        return ret


class patches(object):
    def __call__(self, bug):
        return sum(1 for a in bug['attachments'] if a['is_patch'] or a['content_type'] in ['text/x-review-board-request', 'text/x-phabricator-request'])


class landings(object):
    def __call__(self, bug):
        return sum(1 for c in bug['comments'] if '://hg.mozilla.org/' in c['text'])


class title(object):
    def __call__(self, bug):
        ret = []

        keywords = [
            'fail',
        ]
        for keyword in keywords:
            if keyword in bug['summary'].lower():
                ret.append(keyword)

        return ret


class product(object):
    def __call__(self, bug):
        return bug['product']


class component(object):
    def __call__(self, bug):
        return bug['component']


class is_mozillian(object):
    def __call__(self, bug):
        return any(bug['creator_detail']['email'].endswith(domain) for domain in ['@mozilla.com', '@mozilla.org'])


class commit_added(object):
    def __call__(self, bug):
        return sum(commit['added'] for commit in bug['commits'])


class commit_deleted(object):
    def __call__(self, bug):
        return sum(commit['deleted'] for commit in bug['commits'])


class commit_types(object):
    def __call__(self, bug):
        return sum((commit['types'] for commit in bug['commits']), [])


def cleanup_url(text):
    text = re.sub(r'http[s]?://(hg.mozilla|searchfox|dxr.mozilla)\S+', '__CODE_REFERENCE_URL__', text)
    return re.sub(r'http\S+', '__URL__', text)


def cleanup_fileref(text):
    return re.sub(r'\w+\.py\b|\w+\.json\b|\w+\.js\b|\w+\.jsm\b|\w+\.html\b|\w+\.css\b|\w+\.c\b|\w+\.cpp\b|\w+\.h\b', '__FILE_REFERENCE__', text)


def cleanup_responses(text):
    return re.sub('>[^\n]+', ' ', text)


def cleanup_hex(text):
    return re.sub(r'\b0[xX][0-9a-fA-F]+\b', '__HEX_NUMBER__', text)


def cleanup_dll(text):
    return re.sub(r'\w+(\.dll|\.so|\.dylib)\b', '__DLL_NAME__', text)


def cleanup_synonyms(text):
    synonyms = [
        ('safemode', ['safemode', 'safe mode']),
        ('str', ['str', 'steps to reproduce', 'repro steps']),
        ('uaf', ['uaf', 'use after free', 'use-after-free']),
        ('asan', ['asan', 'address sanitizer', 'addresssanitizer']),
        ('permafailure', ['permafailure', 'permafailing', 'permafail', 'perma failure', 'perma failing', 'perma fail', 'perma-failure', 'perma-failing', 'perma-fail']),
        ('spec', ['spec', 'specification']),
    ]

    for synonym_group, synonym_list in synonyms:
        text = re.sub('|'.join(f'\b{synonym}\b' for synonym in synonym_list), synonym_group, text, flags=re.IGNORECASE)

    return text


def cleanup_crash(text):
    return re.sub(r'bp-[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{6}[0-9]{6}\b', '__CRASH_STATS_LINK__', text)


class BugExtractor(BaseEstimator, TransformerMixin):
    def __init__(self, feature_extractors, cleanup_functions, rollback=False, rollback_when=None, commit_data=False):
        self.feature_extractors = feature_extractors
        self.cleanup_functions = cleanup_functions
        self.rollback = rollback
        self.rollback_when = rollback_when
        self.commit_map = repository.get_commit_map() if commit_data else None

    def fit(self, x, y=None):
        return self

    def transform(self, bugs):
        results = []

        for bug in bugs:
            bug_id = bug['id']

            if self.rollback:
                bug = bug_snapshot.rollback(bug, self.rollback_when)

            data = {}

            if self.commit_map is not None:
                if bug_id in self.commit_map:
                    bug['commits'] = self.commit_map[bug_id]
                else:
                    bug['commits'] = []

            for f in self.feature_extractors:
                res = f(bug)

                if res is None:
                    continue

                if isinstance(res, list):
                    for item in res:
                        data[f.__class__.__name__ + '-' + item] = 'True'
                    continue

                if isinstance(res, bool):
                    res = str(res)

                data[f.__class__.__name__] = res

            # TODO: Try simply using all possible fields instead of extracting features manually.

            for cleanup_function in self.cleanup_functions:
                bug['summary'] = cleanup_function(bug['summary'])
                for c in bug['comments']:
                    c['text'] = cleanup_function(c['text'])

            result = {
                'data': data,
                'title': bug['summary'],
                'first_comment': bug['comments'][0]['text'],
                'comments': ' '.join([c['text'] for c in bug['comments']]),
            }

            results.append(result)

        return pd.DataFrame(results)
