# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import json
import os

import requests
from libmozdata import bugzilla
from tqdm import tqdm

from bugbug import db

BUGS_DB = 'data/bugs.json'
db.register(BUGS_DB, 'https://www.dropbox.com/s/xm6wzac9jl81irz/bugs.json.xz?dl=1')

ATTACHMENT_INCLUDE_FIELDS = [
    'id', 'is_obsolete', 'flags', 'is_patch', 'creator', 'content_type', 'creation_time',
]

COMMENT_INCLUDE_FIELDS = [
    'id', 'text', 'author', 'creation_time',
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
    bugzilla.Bugzilla.TOKEN = token


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

    bugzilla.Bugzilla(ids_or_query, bughandler=bughandler, commenthandler=commenthandler, comment_include_fields=COMMENT_INCLUDE_FIELDS, attachmenthandler=attachmenthandler, attachment_include_fields=ATTACHMENT_INCLUDE_FIELDS, historyhandler=historyhandler).get_data().wait()

    return new_bugs


def download_bugs_between(date_from, date_to, security=False):
    products = {
        'Add-on SDK',
        'Android Background Services',
        'Core',
        'DevTools',
        'External Software Affecting Firefox',
        'Firefox',
        'Firefox for Android',
        # 'Firefox for iOS',
        'Firefox Graveyard',
        'Firefox Health Report',
        # 'Focus',
        # 'Hello (Loop)',
        'NSPR',
        'NSS',
        'Toolkit',
        'WebExtensions',
    }

    r = requests.get(f'https://bugzilla.mozilla.org/rest/bug?include_fields=id&f1=creation_ts&o1=greaterthan&v1={date_from.strftime("%Y-%m-%d")}&limit=1&order=bug_id')
    r.raise_for_status()
    first_id = r.json()['bugs'][0]['id']

    r = requests.get(f'https://bugzilla.mozilla.org/rest/bug?include_fields=id&f1=creation_ts&o1=lessthan&v1={date_to.strftime("%Y-%m-%d")}&limit=1&order=bug_id%20desc')
    r.raise_for_status()
    last_id = r.json()['bugs'][0]['id']

    assert first_id < last_id

    all_ids = range(first_id, last_id + 1)

    download_bugs(all_ids, security=security, products=products)

    return all_ids


def download_bugs(bug_ids, products=None, security=False):
    old_bug_count = 0
    old_bugs = []
    new_bug_ids = {int(bug_id) for bug_id in bug_ids}
    for bug in get_bugs():
        old_bug_count += 1
        if int(bug['id']) in new_bug_ids:
            old_bugs.append(bug)
            new_bug_ids.remove(bug['id'])

    print(f'Loaded {old_bug_count} bugs.')

    new_bug_ids = sorted(list(new_bug_ids))

    chunks = (new_bug_ids[i:(i + 500)] for i in range(0, len(new_bug_ids), 500))
    with tqdm(total=len(new_bug_ids)) as progress_bar:
        for chunk in chunks:
            new_bugs = _download(chunk)

            progress_bar.update(len(chunk))

            if not security:
                new_bugs = {bug_id: bug for bug_id, bug in new_bugs.items() if len(bug['groups']) == 0}

            if products is not None:
                new_bugs = {bug_id: bug for bug_id, bug in new_bugs.items() if bug['product'] in products}

            db.append(BUGS_DB, new_bugs.values())
