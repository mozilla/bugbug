# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import itertools
import json
import os

import requests
from libmozdata import bugzilla

from bugbug import db

BUGS_DB = 'data/bugs.json'
db.register(BUGS_DB, 'https://www.dropbox.com/s/xm6wzac9jl81irz/bugs.json.xz?dl=1')

ATTACHMENT_INCLUDE_FIELDS = [
    'id', 'is_obsolete', 'flags', 'is_patch', 'creator', 'content_type',
]

COMMENT_INCLUDE_FIELDS = [
    'id', 'text', 'author', 'time',
]


def get_bug_fields():
    os.makedirs('data', exist_ok=True)

    try:
        with open('data/bug_fields.json', 'r') as f:
            return json.load(f)
    except IOError:
        pass

    r = requests.get('https://bugzilla.mozilla.org/rest/field/bug')
    return r.json()['fields']


def get_bugs():
    return db.read(BUGS_DB)


def _download(ids_or_query):
    new_bugs = {}

    def bughandler(bug):
        bug_id = int(bug['id'])

        if bug_id not in new_bugs:
            new_bugs[bug_id] = dict()

        for k, v in bug.items():
            new_bugs[bug_id][k] = v

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
    products = [
        'Add-on SDK',
        'Android Background Services',
        'Core',
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
    ]

    query = '&'.join(['product=' + p for p in products]) + '&' +\
            'bug_status=RESOLVED&bug_status=VERIFIED&resolution=FIXED&' +\
            'f1=creation_ts&o1=greaterthan&v1={f}&f2=creation_ts&o2=lessthan&v2={t}&' +\
            'f3=cf_last_resolved&o3=lessthan&v3={t}'
    if not security:
        query += '&f4=bug_group&o4=isempty'
    query = query.format(f=date_from.strftime('%Y-%m-%d'), t=date_to.strftime('%Y-%m-%d'))

    bugs = _download(query)

    db.write(BUGS_DB, bugs.values())


def download_bugs(bug_ids, security=False):
    old_bug_count = 0
    old_bugs = []
    new_bug_ids = set([int(bug_id) for bug_id in bug_ids])
    for bug in get_bugs():
        old_bug_count += 1
        if int(bug['id']) in new_bug_ids:
            old_bugs.append(bug)
            new_bug_ids.remove(bug['id'])

    print('Loaded {} bugs.'.format(old_bug_count))

    print('To download {} bugs.'.format(len(new_bug_ids)))

    new_bugs = _download(new_bug_ids)

    if not security:
        new_bugs = {bug_id: bug for bug_id, bug in new_bugs.items() if len(bug['groups']) == 0}

    print('Total number of bugs: {}'.format(old_bug_count + len(new_bugs)))

    if len(new_bugs):
        db.append(BUGS_DB, new_bugs.values())

    return itertools.chain(old_bugs, new_bugs.items())
