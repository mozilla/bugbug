import csv
import os
import json
from libmozdata import bugzilla
import requests


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


def get_bugs(bug_ids):
    os.makedirs('data', exist_ok=True)

    bugs = {}

    for bug_id in bug_ids:
        try:
            with open('data/' + str(bug_id) + '.json', 'r') as f:
                bugs[bug_id] = json.load(f)
        except IOError:
            continue

    bug_ids = [bug_id for bug_id in bug_ids if bug_id not in bugs]

    print('Loaded ' + str(len(bugs)) + ' bugs.')

    print('To download ' + str(len(bug_ids))+ ' bugs.')

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

    bugzilla.Bugzilla(bug_ids, bughandler=bughandler, commenthandler=commenthandler, comment_include_fields=COMMENT_INCLUDE_FIELDS, attachmenthandler=attachmenthandler, attachment_include_fields=ATTACHMENT_INCLUDE_FIELDS, historyhandler=historyhandler).get_data().wait()

    bugs.update(new_bugs)

    print('Total number of bugs: ' + str(len(bugs)))

    for bug_id, bug_data in new_bugs.items():
        with open('data/' + str(bug_id) + '.json', 'w') as f:
            json.dump(bug_data, f)

    return bugs


def get_labels():
    with open('classes.csv', 'r') as f:
        classes = dict([row for row in csv.reader(f)][1:])

    with open('classes_more.csv', 'r') as f:
        classes_more = [row for row in csv.reader(f)][1:]

    for bug_id, category in classes_more:
        if category == 'nobug':
            is_bug = 'False'
        else:
            is_bug = 'True'

        classes[bug_id] = is_bug

    for bug_id, is_bug in classes.items():
        assert is_bug == 'True' or is_bug == 'False'

    return [(int(bug_id), True if is_bug == 'True' else False) for bug_id, is_bug in classes.items()]
