# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import json
import os

import requests
from libmozdata.bugzilla import Bugzilla
from tqdm import tqdm

from bugbug import db

BUGS_DB = 'data/bugs.json'
db.register(BUGS_DB, 'https://www.dropbox.com/s/vekjzqu4v0pu1v8/bugs.json.xz?dl=1', 'v1')

ATTACHMENT_INCLUDE_FIELDS = [
    'id', 'is_obsolete', 'flags', 'is_patch', 'creator', 'content_type', 'creation_time',
]

COMMENT_INCLUDE_FIELDS = [
    'id', 'count', 'text', 'author', 'creation_time',
]


def get_bug_fields():
    os.makedirs('data', exist_ok=True)

    try:
        with open('data/bug_fields.json', 'r') as f:
            return json.load(f)
    except IOError:
        pass

    r = requests.get('https://bugzilla.mozilla.org/rest/field/bug')
    r.raise_for_status()
    return r.json()['fields']


def get_bugs():
    return db.read(BUGS_DB)


def set_token(token):
    Bugzilla.TOKEN = token


def _download(ids_or_query):
    new_bugs = {}

    def bughandler(bug):
        bug_id = int(bug['id'])

        if bug_id not in new_bugs:
            new_bugs[bug_id] = dict()

        new_bugs[bug_id].update(bug)

    def commenthandler(bug, bug_id):
        bug_id = int(bug_id)

        if bug_id not in new_bugs:
            new_bugs[bug_id] = dict()

        new_bugs[bug_id]['comments'] = bug['comments']

    def attachmenthandler(bug, bug_id):
        bug_id = int(bug_id)

        if bug_id not in new_bugs:
            new_bugs[bug_id] = dict()

        new_bugs[bug_id]['attachments'] = bug

    def historyhandler(bug):
        bug_id = int(bug['id'])

        if bug_id not in new_bugs:
            new_bugs[bug_id] = dict()

        new_bugs[bug_id]['history'] = bug['history']

    Bugzilla(ids_or_query, bughandler=bughandler, commenthandler=commenthandler, comment_include_fields=COMMENT_INCLUDE_FIELDS, attachmenthandler=attachmenthandler, attachment_include_fields=ATTACHMENT_INCLUDE_FIELDS, historyhandler=historyhandler).get_data().wait()

    return new_bugs


def download_bugs_between(date_from, date_to, security=False):
    products = {
        'Add-on SDK',
        'Android Background Services',
        'Core',
        'Core Graveyard',
        'DevTools',
        'DevTools Graveyard',
        'External Software Affecting Firefox',
        'Firefox',
        'Firefox Graveyard',
        'Firefox Build System',
        'Firefox for Android',
        'Firefox for Android Graveyard',
        # 'Firefox for iOS',
        'Firefox Health Report',
        # 'Focus',
        # 'Hello (Loop)',
        'NSPR',
        'NSS',
        'Toolkit',
        'Toolkit Graveyard',
        'WebExtensions',
    }

    params = {
        'f1': 'creation_ts', 'o1': 'greaterthan', 'v1': date_from.strftime('%Y-%m-%d'),
        'f2': 'creation_ts', 'o2': 'lessthan', 'v2': date_to.strftime('%Y-%m-%d'),
        'product': products,
    }

    if not security:
        params['f3'] = 'bug_group'
        params['o3'] = 'isempty'

    params['count_only'] = 1
    r = requests.get('https://bugzilla.mozilla.org/rest/bug', params=params)
    r.raise_for_status()
    count = r.json()['bug_count']
    del params['count_only']

    params['limit'] = 100
    params['order'] = 'bug_id'

    old_bug_ids = set(bug['id'] for bug in get_bugs())

    all_bugs = []

    with tqdm(total=count) as progress_bar:
        for offset in range(0, count, Bugzilla.BUGZILLA_CHUNK_SIZE):
            params['offset'] = offset

            new_bugs = _download(params)

            progress_bar.update(Bugzilla.BUGZILLA_CHUNK_SIZE)

            all_bugs += [bug for bug in new_bugs.values()]

            db.append(BUGS_DB, (bug for bug_id, bug in new_bugs.items() if bug_id not in old_bug_ids))

    return all_bugs


def download_bugs(bug_ids, products=None, security=False):
    old_bug_count = 0
    old_bugs = []
    new_bug_ids = set(int(bug_id) for bug_id in bug_ids)
    for bug in get_bugs():
        old_bug_count += 1
        if int(bug['id']) in new_bug_ids:
            old_bugs.append(bug)
            new_bug_ids.remove(bug['id'])

    print(f'Loaded {old_bug_count} bugs.')

    new_bug_ids = sorted(list(new_bug_ids))

    CHUNK_SIZE = 100

    chunks = (new_bug_ids[i:(i + CHUNK_SIZE)] for i in range(0, len(new_bug_ids), CHUNK_SIZE))
    with tqdm(total=len(new_bug_ids)) as progress_bar:
        for chunk in chunks:
            new_bugs = _download(chunk)

            progress_bar.update(len(chunk))

            if not security:
                new_bugs = {bug_id: bug for bug_id, bug in new_bugs.items() if len(bug['groups']) == 0}

            if products is not None:
                new_bugs = {bug_id: bug for bug_id, bug in new_bugs.items() if bug['product'] in products}

            db.append(BUGS_DB, new_bugs.values())


def delete_bugs(bug_ids):
    db.delete(BUGS_DB, lambda bug: bug['id'] in set(bug_ids))
