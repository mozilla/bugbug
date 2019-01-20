# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import dateutil.parser


def bool_str(val):
    assert val in ['0', '1']

    return True if val == '1' else False


def keyword_mapping(keyword):
    mapping = {
        'mlk': 'memory-leak',
        'topmlk': 'top-memory-leak',
        'pp': 'platform-parity',
        'footprint': 'memory-footprint',
        'ateam-marionette-firefox-puppeteer': 'pi-marionette-firefox-puppeteer',
    }

    return mapping[keyword] if keyword in mapping else keyword


def group_mapping(group):
    mapping = {
        'release-core-security': 'core-security-release',
    }

    return mapping[group] if group in mapping else group


def cf_rank(val):
    if val in ['', '0']:
        return None

    return val


# E.g. https://bugzilla.mozilla.org/rest/bug/1162372.
def version_to_branch(version):
    if version.startswith('Firefox '):
        return f'{version[len("Firefox "):]} Branch'

    return version


FIELD_TYPES = {
    'blocks': int,
    'depends_on': int,
    'is_confirmed': bool_str,
    'is_cc_accessible': bool_str,
    'is_creator_accessible': bool_str,
    'cf_rank': cf_rank,
    'keywords': keyword_mapping,
    'groups': group_mapping,
}


def is_email(val):
    return isinstance(val, str) and '@' in val


def parse_flag_change(change):
    parts = change.split('(')
    assert len(parts) == 1 or len(parts) == 2, f'Too many parts for {change}'
    name_and_status = parts[0]
    name = name_and_status[:-1]
    status = name_and_status[-1]
    assert status in ['?', '+', '-'], f'unexpected status: {status}'
    requestee = None if len(parts) != 2 else parts[1][:-1]
    return name, status, requestee


def rollback(bug, when):
    newest_product = bug['product']

    change_to_return = None
    if when is not None:
        for history in bug['history']:
            for change in history['changes']:
                if when(change):
                    change_to_return = change
                    rollback_date = dateutil.parser.parse(history['when'])
                    break

            if change_to_return is not None:
                break

        if change_to_return is None:
            return bug
    else:
        rollback_date = dateutil.parser.parse(bug['creation_time'])

    ret = False

    for history in reversed(bug['history']):
        # TODO: Handle changes to product and component.
        # TODO: This code might be removed when https://bugzilla.mozilla.org/show_bug.cgi?id=1513952 is fixed.
        pass

        if ret:
            break

        for change in history['changes']:
            if change is change_to_return:
                ret = True
                break

            field = change['field_name']

            # TODO: These products no longer have the cf_has_str and cf_has_regression_range field (https://bugzilla.mozilla.org/show_bug.cgi?id=1513981)
            if newest_product in ['DevTools', 'DevTools Graveyard', 'NSS', 'Tech Evangelism', 'Firefox Build System', 'WebExtensions', 'Firefox Graveyard'] and field in ['cf_has_regression_range', 'cf_has_str']:
                continue

            if field in 'component':
                # TODO: Ignore this for now, not so easy to make it work https://bugzilla.mozilla.org/show_bug.cgi?id=1513952.
                continue

            if field == 'qa_contact':
                # TODO: Ignore this for now. Example usage in 92144.
                continue

            if field == 'cf_fx_iteration':
                # TODO: Ignore this for now. Example usage in 1101478.
                continue

            if field == 'cf_crash_signature':
                # TODO: Ignore this for now. Example usage in 1437575.
                continue

            if field == 'cf_backlog':
                # TODO: Ignore this for now. Example usage in 1048455.
                continue

            if field == 'bug_mentor':
                # TODO: Ignore this for now. Example usage in 1042103.
                continue

            if field == 'cf_user_story':
                # TODO: Ignore this for now. Example usage in 1369255.
                # Seems to be broken in Bugzilla.
                continue

            if field == 'cf_rank':
                # TODO: Ignore this for now. Example usage in 1475099.
                continue

            if field in ['alias', 'restrict_comments']:
                continue

            # TODO: Remove when https://bugzilla.mozilla.org/show_bug.cgi?id=1513956 and https://bugzilla.mozilla.org/show_bug.cgi?id=1513995 are fixed.
            if field in ['summary', 'whiteboard']:
                change['added'] = change['added'].rstrip()
                change['removed'] = change['removed'].rstrip()
                bug[field] = bug[field].rstrip()

            if field == 'longdescs.isprivate':
                # Ignore for now.
                continue

            if field == 'version':
                # TODO: Ignore this for now. Example usage in 1162372 or 1389926.
                continue

            if 'attachment_id' in change and field.startswith('attachments'):
                # TODO: Ignore changes to attachments for now.
                continue

            if field == 'flagtypes.name':
                if 'attachment_id' in change:
                    # https://bugzilla.mozilla.org/show_bug.cgi?id=1516172
                    if bug['id'] == 1421395:
                        continue

                    obj = None
                    for attachment in bug['attachments']:
                        if attachment['id'] == change['attachment_id']:
                            obj = attachment
                            break
                    assert obj is not None
                else:
                    obj = bug

                if change['added']:
                    for to_remove in change['added'].split(', '):
                        if to_remove.startswith('approval-comm-beta'):
                            # Skip this for now.
                            continue

                        if any(to_remove.startswith(s) for s in ['needinfo', 'review', 'feedback', 'ui-review', 'sec-approval']):
                            # TODO: Skip needinfo/reviews for now, we need a way to match them precisely when there are multiple needinfos/reviews requested.
                            continue

                        name, status, requestee = parse_flag_change(to_remove)

                        found_flag = None
                        for f in obj['flags']:
                            if f['name'] == name and f['status'] == status and (requestee is None or f['requestee'] == requestee):
                                assert found_flag is None, f'{f["name"]}{f["status"]}{f["requestee"]} found twice!'
                                found_flag = f

                        # TODO: always assert here, once https://bugzilla.mozilla.org/show_bug.cgi?id=1514415 is fixed.
                        if obj['id'] not in [1052536, 1201115, 1213517]:
                            assert found_flag is not None, f'flag {to_remove} not found'
                        if found_flag is not None:
                            obj['flags'].remove(found_flag)

                if change['removed']:
                    for to_add in change['removed'].split(', '):
                        name, status, requestee = parse_flag_change(to_add)

                        new_flag = {
                            'name': name,
                            'status': status,
                        }
                        if requestee is not None:
                            new_flag['requestee'] = requestee

                        obj['flags'].append(new_flag)

                continue

            if change['added'] != '---':
                if field not in bug:
                    # TODO: try to remove when https://bugzilla.mozilla.org/show_bug.cgi?id=1514002 is fixed.
                    if any(field.startswith(k) for k in ['cf_status_', 'cf_tracking_']):
                        print(f'{field} is not in bug')
                    else:
                        assert False, f'{field} is not in bug'

            if field in bug and isinstance(bug[field], list):
                if change['added']:
                    if field == 'see_also' and change['added'].endswith(', '):
                        change['added'] = change['added'][:-2]

                    for to_remove in change['added'].split(', '):
                        if field in FIELD_TYPES:
                            to_remove = FIELD_TYPES[field](to_remove)

                        if is_email(to_remove):
                            # TODO: Users can change their email, try with all emails from a mapping file.
                            continue

                        if to_remove in ['checkin-needed', '#relman/triage/defer-to-group']:
                            # TODO: https://bugzilla.mozilla.org/show_bug.cgi?id=1513981.
                            if to_remove in bug[field]:
                                bug[field].remove(to_remove)
                            continue

                        assert to_remove in bug[field], f'{to_remove} is not in {bug[field]}, for field {field}'
                        bug[field].remove(to_remove)

                if change['removed']:
                    for to_add in change['removed'].split(', '):
                        if field in FIELD_TYPES:
                            to_add = FIELD_TYPES[field](to_add)
                        bug[field].append(to_add)
            else:
                if field in FIELD_TYPES:
                    old_value = FIELD_TYPES[field](change['removed'])
                    new_value = FIELD_TYPES[field](change['added'])
                else:
                    old_value = change['removed']
                    new_value = change['added']

                # TODO: Users can change their email, try with all emails from a mapping file.
                if field in bug and not is_email(bug[field]):
                    if bug[field] != new_value:
                        # TODO: try to remove when https://bugzilla.mozilla.org/show_bug.cgi?id=1514002 is fixed.
                        if any(field.startswith(k) for k in ['cf_status_', 'cf_tracking_']):
                            print(f'Current value for field {field}:\n{bug[field]}\nis different from previous value:\n{new_value}')
                        else:
                            assert False, f'Current value for field {field}:\n{bug[field]}\nis different from previous value:\n{new_value}'

                bug[field] = old_value

    bug['comments'] = [c for c in bug['comments'] if dateutil.parser.parse(c['creation_time']) <= rollback_date]
    bug['attachments'] = [a for a in bug['attachments'] if dateutil.parser.parse(a['creation_time']) <= rollback_date]

    return bug


if __name__ == '__main__':
    from bugbug import bugzilla

    for i, bug in enumerate(bugzilla.get_bugs()):
        print(bug['id'])
        print(i)
        rollback(bug, None)
