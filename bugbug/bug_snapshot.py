# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import dateutil.parser

from bugbug import bugzilla


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
        'ateam-marionette-big': 'pi-marionette-big',
        'ateam-marionette-runner': 'pi-marionette-runner',
        'ateam-marionette-server': 'pi-marionette-server',
        'ateam-marionette-client': 'pi-marionette-client',
        'ateam-marionette-intermittent': 'pi-marionette-intermittent',
        'csec-dos': 'csectype-dos',
        'csec-oom': 'csectype-oom',
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


def op_sys(op_sys):
    if op_sys == 'Mac OS X':
        return 'macOS'

    return op_sys


def product(product):
    mapping = {
        'Web Compatibility Tools': 'Web Compatibility',
        'Mozilla Developer Network': 'developer.mozilla.org',
        'MozReview': 'MozReview Graveyard',
        'mozilla.org graveyard': 'mozilla.org Graveyard',
        'TaskCluster': 'Taskcluster',
        'Firefox OS': 'Firefox OS Graveyard',
        'Add-on SDK': 'Add-on SDK Graveyard',
        'Connected Devices': 'Connected Devices Graveyard',
    }

    return mapping[product] if product in mapping else product


def target_milestone(target_milestone):
    if target_milestone.startswith('Seamonkey'):
        return target_milestone.lower()

    mapping = {
        '6.2.2': '6.2.2.1',
    }

    return mapping[target_milestone] if target_milestone in mapping else target_milestone


FIELD_TYPES = {
    'blocks': int,
    'depends_on': int,
    'regressed_by': int,
    'regressions': int,
    'is_confirmed': bool_str,
    'is_cc_accessible': bool_str,
    'is_creator_accessible': bool_str,
    'cf_rank': cf_rank,
    'keywords': keyword_mapping,
    'groups': group_mapping,
    'op_sys': op_sys,
    'product': product,
    'target_milestone': target_milestone,
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


def is_expected_inconsistent_field(field, last_product, bug_id):
    # TODO: Remove the Graveyard case when https://bugzilla.mozilla.org/show_bug.cgi?id=1541926 is fixed.
    return \
        (field.startswith('cf_') and last_product == 'Firefox for Android Graveyard') or\
        (field == 'cf_tracking_firefox59' and bug_id in [1443367, 1443630]) or\
        (field == 'cf_status_firefox60' and bug_id in [1442627, 1443505, 1443599, 1443600, 1443603, 1443605, 1443608, 1443609, 1443611, 1443614, 1443615, 1443617, 1443644]) or\
        (field in ['cf_has_str', 'cf_has_regression_range'] and bug_id == 1440338)


def is_expected_inconsistent_change_field(field, bug_id, new_value):
    # The 'enhancement' severity has been removed, but it doesn't show up in the history.
    # See https://bugzilla.mozilla.org/show_bug.cgi?id=1541362.
    return \
        (field in ['status', 'resolution', 'cf_last_resolved'] and bug_id == 1312722) or\
        (field == 'cf_last_resolved' and bug_id == 1321567) or\
        (field == 'url' and bug_id == 740223) or\
        (field == 'severity' and new_value == 'enhancement') or\
        (field == 'cf_status_firefox_esr52' and bug_id in [1436341, 1443518, 1443637]) or\
        (field == 'cf_status_firefox57' and bug_id in [1328936, 1381197, 1382577, 1382605, 1382606, 1382607, 1382609, 1383711, 1387511, 1394996, 1403927, 1403977, 1404917, 1406290, 1407347, 1409651, 1410351]) or\
        (field == 'cf_status_firefox58' and bug_id in [1328936, 1383870, 1394996, 1397772, 1408468, 1418410, 1436341, 1441537, 1443511, 1443518, 1443527, 1443544, 1443612, 1443630, 1443637]) or\
        (field == 'cf_status_firefox59' and bug_id in [1328936, 1394996, 1397772, 1403334, 1428996, 1431306, 1436341, 1441537, 1443511, 1443518, 1443527, 1443533, 1443544, 1443612, 1443630, 1443637]) or\
        (field == 'cf_status_firefox60' and bug_id in [1362303, 1363862, 1375913, 1390583, 1401847, 1402845, 1414901, 1421387, 1434483, 1434869, 1436287, 1437803, 1438608, 1440146, 1441052, 1442160, 1442186, 1442861, 1443205, 1443368, 1443371, 1443438, 1443507, 1443511, 1443518, 1443525, 1443527, 1443528, 1443533, 1443560, 1443578, 1443585, 1443593, 1443612, 1443630, 1443637, 1443646, 1443650, 1443651, 1443664]) or\
        (field == 'cf_tracking_firefox60' and bug_id in [1375913, 1439875]) or\
        (field == 'priority' and bug_id == 1337747)


def rollback(bug, when, verbose=True, all_inconsistencies=False):
    last_product = bug['product']

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
                        if obj['id'] not in [1052536, 1201115, 1213517, 794863] and not (to_remove == 'in-testsuite+' and obj['id'] in [1318438, 1312852, 1332255, 1344690, 1362387, 1380306]) and not (to_remove == 'in-testsuite-' and bug['id'] in [1321444, 1342431, 1370129]) and not (to_remove == 'approval-comm-esr52?' and bug['id'] == 1352850) and not (to_remove == 'checkin+' and bug['id'] in [1308868, 1357808, 1361361, 1365763, 1328454]) and not (to_remove == 'checkin-' and bug['id'] == 1412952) and not (to_remove == 'webcompat?' and obj['id'] in [1360579, 1364598]) and not (to_remove == 'qe-verify-' and bug['id'] in [1322685, 1336510, 1363358, 1370506, 1374024, 1377911, 1393848, 1396334, 1398874, 1419371]):
                            assert found_flag is not None, f'flag {to_remove} not found in {bug["id"]}'
                        if found_flag is not None:
                            obj['flags'].remove(found_flag)

                if change['removed']:
                    # Inconsistent review flag.
                    if bug['id'] == 1342178:
                        continue

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
                    if not all_inconsistencies and is_expected_inconsistent_field(field, last_product, bug['id']):
                        if verbose:
                            print(f'{field} is not in bug {bug["id"]}')
                    else:
                        assert False, f'{field} is not in bug {bug["id"]}'

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

                        if to_remove in ['checkin-needed', '#relman/triage/defer-to-group', 'conduit-needs-discussion']:
                            # TODO: https://bugzilla.mozilla.org/show_bug.cgi?id=1513981.
                            if to_remove in bug[field]:
                                bug[field].remove(to_remove)
                            continue

                        assert to_remove in bug[field], f'{to_remove} is not in {bug[field]}, for field {field} of {bug["id"]}'
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
                        if not all_inconsistencies and is_expected_inconsistent_change_field(field, bug['id'], new_value):
                            print(f'Current value for field {field} of {bug["id"]}:\n{bug[field]}\nis different from previous value:\n{new_value}')
                        else:
                            assert False, f'Current value for field {field} of {bug["id"]}:\n{bug[field]}\nis different from previous value:\n{new_value}'

                bug[field] = old_value

    # If the first comment is hidden.
    if bug['comments'][0]['count'] != 0:
        bug['comments'].insert(0, {
            'id': 0,
            'text': '',
            'author': bug['creator'],
            'creation_time': bug['creation_time'],
        })

    bug['comments'] = [c for c in bug['comments'] if dateutil.parser.parse(c['creation_time']) <= rollback_date]
    bug['attachments'] = [a for a in bug['attachments'] if dateutil.parser.parse(a['creation_time']) <= rollback_date]

    assert len(bug['comments']) >= 1

    return bug


def get_inconsistencies(find_all=False):
    inconsistencies = []

    for bug in bugzilla.get_bugs():
        try:
            rollback(bug, None, False, find_all)
        except Exception as e:
            print(bug['id'])
            print(e)
            inconsistencies.append(bug['id'])

    return inconsistencies


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--verbose', help='Verbose mode', action='store_true')
    args = parser.parse_args()

    for i, bug in enumerate(bugzilla.get_bugs()):
        if args.verbose:
            print(bug['id'])
            print(i)
        rollback(bug, None, False)
