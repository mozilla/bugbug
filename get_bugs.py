import os
import json
from libmozdata import bugzilla


ATTACHMENT_INCLUDE_FIELDS = [
    'id', 'is_obsolete', 'flags', 'is_patch', 'creator', 'content_type',
]

COMMENT_INCLUDE_FIELDS = [
    'id', 'text', 'author', 'time',
]


# Assumes query doesn't use the f1 field.
def get_bugs(bug_ids):
    try:
        os.mkdir('data')
    except OSError:
        pass

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

    def bughandler(bug):
        bug_id = int(bug['id'])

        if bug_id not in bugs:
            bugs[bug_id] = dict()

        for k, v in bug.items():
            bugs[bug_id][k] = v

    def commenthandler(bug, bug_id):
        bug_id = int(bug_id)

        if bug_id not in bugs:
            bugs[bug_id] = dict()

        bugs[bug_id]['comments'] = bug['comments']

    def attachmenthandler(bug, bug_id):
        bug_id = int(bug_id)

        if bug_id not in bugs:
            bugs[bug_id] = dict()

        bugs[bug_id]['attachments'] = bug

    def historyhandler(bug):
        bug_id = int(bug['id'])

        if bug_id not in bugs:
            bugs[bug_id] = dict()

        bugs[bug_id]['history'] = bug['history']

    bugzilla.Bugzilla(bug_ids, bughandler=bughandler, commenthandler=commenthandler, comment_include_fields=COMMENT_INCLUDE_FIELDS, attachmenthandler=attachmenthandler, attachment_include_fields=ATTACHMENT_INCLUDE_FIELDS, historyhandler=historyhandler).get_data().wait()

    print('Total number of bugs: ' + str(len(bugs)))

    for bug_id, bug_data in bugs.items():
        with open('data/' + str(bug_id) + '.json', 'w') as f:
            json.dump(bug_data, f)

    return bugs
