# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import dateutil.parser
from dateutil.relativedelta import relativedelta

from bugbug import bugzilla


def bool_str(val):
    assert val in ["", "0", "1"], f"Unexpected boolean value: '{val}'"

    return val == "1"


def keyword_mapping(keyword):
    mapping = {
        "mlk": "memory-leak",
        "topmlk": "top-memory-leak",
        "pp": "platform-parity",
        "footprint": "memory-footprint",
        "ateam-marionette-firefox-puppeteer": "pi-marionette-firefox-puppeteer",
        "ateam-marionette-big": "pi-marionette-big",
        "ateam-marionette-runner": "pi-marionette-runner",
        "ateam-marionette-server": "pi-marionette-server",
        "ateam-marionette-client": "pi-marionette-client",
        "ateam-marionette-intermittent": "pi-marionette-intermittent",
        "ateam-marionette-harness-tests": "pi-marionette-harness-tests",
        "ateam-marionette-spec": "pi-marionette-spec",
        "csec-dos": "csectype-dos",
        "csec-oom": "csectype-oom",
        "csec-bounds": "csectype-bounds",
        "bug-quality": "bmo-bug-quality",
    }

    return mapping[keyword] if keyword in mapping else keyword


def group_mapping(group):
    mapping = {"release-core-security": "core-security-release"}

    return mapping[group] if group in mapping else group


def cf_rank(val):
    if val in ["", "0"]:
        return None

    return val


# E.g. https://bugzilla.mozilla.org/rest/bug/1162372.
def version_to_branch(version):
    if version.startswith("Firefox "):
        return f'{version[len("Firefox "):]} Branch'

    return version


def op_sys(op_sys):
    if op_sys == "Mac OS X":
        return "macOS"

    return op_sys


def platform(platform):
    if platform == "Macintosh":
        return "PowerPC"
    elif platform == "PC":
        return "x86"

    return platform


def product(product):
    mapping = {
        "Web Compatibility Tools": "Web Compatibility",
        "Mozilla Developer Network": "developer.mozilla.org",
        "MozReview": "MozReview Graveyard",
        "mozilla.org graveyard": "mozilla.org Graveyard",
        "TaskCluster": "Taskcluster",
        "Firefox OS": "Firefox OS Graveyard",
        "Add-on SDK": "Add-on SDK Graveyard",
        "Connected Devices": "Connected Devices Graveyard",
        "Seamonkey": "SeaMonkey",
        "Mozilla Application Suite": "SeaMonkey",
        "Mozilla Services": "Cloud Services",
        "Browser": "Core",
    }

    return mapping[product] if product in mapping else product


def target_milestone(target_milestone):
    if target_milestone.startswith("Seamonkey"):
        return target_milestone.lower()

    mapping = {"6.2.2": "6.2.2.1", "Firefox 3.7": "Firefox 4.0"}

    return (
        mapping[target_milestone] if target_milestone in mapping else target_milestone
    )


def null_str(val):
    if val == "":
        return None

    return val


FIELD_TYPES = {
    "blocks": int,
    "depends_on": int,
    "regressed_by": int,
    "regressions": int,
    "is_confirmed": bool_str,
    "is_cc_accessible": bool_str,
    "is_creator_accessible": bool_str,
    "cf_rank": cf_rank,
    "keywords": keyword_mapping,
    "groups": group_mapping,
    "op_sys": op_sys,
    "platform": platform,
    "product": product,
    "target_milestone": target_milestone,
    "cf_due_date": null_str,
}


def is_email(val):
    return isinstance(val, str) and "@" in val


def is_expected_inconsistent_field(field, last_product, bug_id):
    # TODO: Remove the Graveyard case when https://bugzilla.mozilla.org/show_bug.cgi?id=1541926 is fixed.
    return (
        (field.startswith("cf_") and last_product == "Firefox for Android Graveyard")
        or (field == "cf_status_firefox_esr52" and bug_id == 1280099)
        or (
            field == "cf_status_firefox57"
            and bug_id
            in (1382577, 1382605, 1382606, 1382607, 1382609, 1394996, 1406290, 1407347)
        )
        or (field == "cf_status_firefox58" and bug_id in {1280099, 1328936, 1394996})
        or (field == "cf_status_firefox59" and bug_id in {1280099, 1328936, 1394996})
        or (
            field == "cf_tracking_firefox59"
            and bug_id in (1328936, 1394996, 1_443_367, 1_443_630)
        )
        or (
            field == "cf_status_firefox60"
            and bug_id
            in [
                1_442_627,
                1_443_505,
                1_443_599,
                1_443_600,
                1_443_603,
                1_443_605,
                1_443_608,
                1_443_609,
                1_443_611,
                1_443_614,
                1_443_615,
                1_443_617,
                1_443_644,
                1443466,
            ]
        )
        or (field == "cf_has_str" and bug_id == 1_440_338)
        or (field == "cf_has_regression_range")  # the field was removed
    )


def is_expected_inconsistent_change_field(field, bug_id, new_value, new_value_exp):
    # The 'enhancement' severity has been removed, but it doesn't show up in the history.
    # See https://bugzilla.mozilla.org/show_bug.cgi?id=1541362.
    return (
        (field in ["status", "resolution", "cf_last_resolved"] and bug_id == 1_312_722)
        or (field == "cf_last_resolved" and bug_id == 1_321_567)
        or (
            field == "url"
            and bug_id
            in (
                380637,
                740_223,
                1326518,
                1335350,
                1340490,
                1378065,
                1381475,
                1389540,
                1395484,
                1403353,
            )
        )
        or (field == "severity" and new_value == "enhancement")
        or (field == "cf_blocking_20" and bug_id == 380637)
        or (field == "cf_blocking_191" and bug_id in (471015, 607222))
        or (field == "cf_blocking_192" and bug_id == 607222)
        or (field == "cf_status_firefox_esr45" and bug_id == 1292534)
        or (
            field == "cf_status_firefox_esr52"
            and bug_id in [1_436_341, 1_443_518, 1_443_637]
        )
        or (
            field == "cf_status_firefox57"
            and bug_id
            in [
                1_328_936,
                1_381_197,
                1_382_577,
                1_382_605,
                1_382_606,
                1_382_607,
                1_382_609,
                1_383_711,
                1_387_511,
                1_394_996,
                1_403_927,
                1_403_977,
                1_404_917,
                1_406_290,
                1_407_347,
                1_409_651,
                1_410_351,
            ]
        )
        or (
            field == "cf_status_firefox58"
            and bug_id
            in [
                1_328_936,
                1_383_870,
                1_394_996,
                1_397_772,
                1_408_468,
                1_418_410,
                1_436_341,
                1_441_537,
                1_443_511,
                1_443_518,
                1_443_527,
                1_443_544,
                1_443_612,
                1_443_630,
                1_443_637,
            ]
        )
        or (
            field == "cf_status_firefox59"
            and bug_id
            in [
                1_328_936,
                1_394_996,
                1_397_772,
                1_403_334,
                1_428_996,
                1_431_306,
                1_436_341,
                1_441_537,
                1_443_511,
                1_443_518,
                1_443_527,
                1_443_533,
                1_443_544,
                1_443_612,
                1_443_630,
                1_443_637,
            ]
        )
        or (
            field == "cf_status_firefox60"
            and bug_id
            in [
                851471,
                1280099,
                1_362_303,
                1_363_862,
                1_375_913,
                1_390_583,
                1_401_847,
                1_402_845,
                1_414_901,
                1_421_387,
                1_434_483,
                1_434_869,
                1_436_287,
                1_437_803,
                1_438_608,
                1_440_146,
                1_441_052,
                1_442_160,
                1_442_186,
                1_442_861,
                1_443_205,
                1_443_368,
                1_443_371,
                1_443_438,
                1_443_507,
                1_443_511,
                1_443_518,
                1_443_525,
                1_443_527,
                1_443_528,
                1_443_533,
                1_443_560,
                1_443_578,
                1_443_585,
                1_443_593,
                1_443_612,
                1_443_630,
                1_443_637,
                1_443_646,
                1_443_650,
                1_443_651,
                1_443_664,
                1442543,
                1443180,
                1443446,
                1443522,
            ]
        )
        or (field == "cf_tracking_firefox60" and bug_id in [1_375_913, 1_439_875])
        or (field == "priority" and bug_id in {1215089, 1_337_747})
        or (
            field == "type" and bug_id == 1540796
        )  # TODO: Remove once https://bugzilla.mozilla.org/show_bug.cgi?id=1550120 is fixed.
        or (
            field == "cf_last_resolved" and bug_id == 1540998
        )  # TODO: Remove once https://bugzilla.mozilla.org/show_bug.cgi?id=1550128 is fixed.
        or (
            field == "type" and bug_id == 1257155
        )  # TODO: Remove once https://bugzilla.mozilla.org/show_bug.cgi?id=1550129 is fixed.
        or (bug_id == 1370035 and field == "cf_has_str")
        or (bug_id == 1400540 and field in ("target_milestone", "status", "resolution"))
        or (bug_id == 1402929 and field == "priority")
        or (field == "whiteboard" and new_value.rstrip() == new_value_exp.rstrip())
        or (
            field == "summary"
            and bug_id
            in (
                1326589,
                1350800,
                1368617,
                1396657,
                1399203,
                1405388,
                1405496,
                1438422,
                1440635,
                1447653,
                1452217,
                1453584,
                1462986,
                1467331,
                1478399,
                1482142,
                1495267,
                1500185,
                1510849,
                1531130,
                1578734,
                1591051,
            )
        )  # https://bugzilla.mozilla.org/show_bug.cgi?id=1556319
        or (field == "whiteboard" and bug_id in (1385923, 1340867))
        or (
            field == "url"
            and bug_id
            in (1362789, 1364792, 1431604, 1437528, 1445898, 1446685, 1460828, 1494587)
        )
        or (field in ("platform", "op_sys") and bug_id == 568516)
        or (
            field == "target_milestone"
            and bug_id in {11050, 19462, 106327, 107264, 144795, 306730}
        )
        or (field == "product" and bug_id in {21438, 263013})
        or is_email(
            new_value
        )  # TODO: Users can change their email, try with all emails from a mapping file.
    )


def is_expected_inconsistent_change_list_field(field, bug_id, value):
    return (
        (
            field == "keywords"
            and value == "checkin-needed"
            and bug_id in [1274602, 1341727]
        )
        or (
            field == "keywords"
            and value
            in [
                "patch",
                "nsbeta1",
                "nsbeta1+",
                "nsbeta1-",
                "nsbeta3",
                "mozilla1.1",
                "mozilla1.0",
                "4xp",
                "sec-review-complete",
                "beta1",
                "beta2",
                "mozilla0.9",
                "mozilla0.9.2",
                "verified1.0.1",
                "fixed1.9.0.10",
                "adt1.0.1+",
                "mail3",
            ]
        )  # These keywords don't exist anymore.
        or is_email(
            value
        )  # TODO: Users can change their email, try with all emails from a mapping file.
    )


def is_expected_inconsistent_change_flag(flag, obj_id):
    # TODO: always assert here, once https://bugzilla.mozilla.org/show_bug.cgi?id=1514415 is fixed.
    return (
        obj_id in [1_052_536, 1_201_115, 1_213_517, 794_863]
        or (
            flag == "in-testsuite+"
            and obj_id
            in [1_318_438, 1_312_852, 1_332_255, 1_344_690, 1_362_387, 1_380_306]
        )
        or (flag == "in-testsuite-" and obj_id in {906177, 1321444, 1342431, 1370129})
        or (
            flag == "checkin+"
            and obj_id
            in {
                8795236,
                8795632,
                8791622,
                8794855,
                8795623,
                8792801,
                8791937,
                8795246,
                8795282,
                8786330,
                8786345,
                8787093,
                8795228,
                8795333,
                8880381,
                8879995,
                8872652,
                8871000,
                8870452,
                8870505,
                8864140,
                8868787,
            }
        )
        or (flag == "checkin-" and obj_id == 8924974)
        or (
            flag == "webcompat?"
            and obj_id
            in (
                1360579,
                1326028,
                1356114,
                1360238,
                1364598,
                1367657,
                1375319,
                1382724,
                1397981,
                1401593,
                1405744,
                1416728,
                1417293,
                1428263,
                1469747,
                1522872,
                1531758,
            )
        )
        or (
            flag == "webcompat+"
            and obj_id in (1294490, 1443958, 1455894, 1456313, 1489308)
        )
        or (flag == "webcompat-" and obj_id == 1419848)
        or (flag == "qe-verify+" and obj_id in {1567624, 1572197})
        or (
            flag == "qe-verify-"
            and obj_id
            in [
                1282408,
                1322685,
                1336510,
                1363358,
                1370506,
                1374024,
                1377911,
                1393848,
                1396334,
                1398874,
                1419371,
            ]
        )
        or (flag == "approval-comm-beta+" and obj_id == 8972248)
        or (flag == "testcase+" and obj_id == 267645)
        or (
            flag
            in [
                "platform-rel?",
                "blocking0.3-",
                "blocking-aviary1.0RC1-",
                "blocking-aviary1.1+",
                "blocking-firefox3.1-",
                "blocking1.8b4-",
                "blocking1.9+",
                "blocking1.9.0.3?",
                "blocking1.9.0.10+",
                "blocking1.9.0.17+",
                "wanted-firefox3.1?",
                "approval1.7.x+",
                "approval1.9.0.10+",
            ]
        )  # These flags have been removed.
    )


def rollback(bug, when=None, do_assert=False):
    def assert_or_log(msg):
        msg = f'{msg}, in bug {bug["id"]}'
        if do_assert:
            assert False, msg
        else:
            print(msg)

    def parse_flag_change(change):
        parts = change.split("(")
        if len(parts) != 1 and len(parts) != 2:
            assert_or_log(f"Too many parts for {change}")
            return None, None, None

        name_and_status = parts[0]
        name = name_and_status[:-1]
        status = name_and_status[-1]
        if status not in ["?", "+", "-"]:
            assert_or_log(f"unexpected status: {status}")
            return None, None, None

        requestee = None if len(parts) != 2 else parts[1][:-1]
        return name, status, requestee

    last_product = bug["product"]

    change_to_return = None
    if when is not None:
        for history in bug["history"]:
            for change in history["changes"]:
                if when(change):
                    change_to_return = change
                    rollback_date = dateutil.parser.parse(history["when"])
                    break

            if change_to_return is not None:
                break

        if change_to_return is None:
            return bug
    else:
        rollback_date = dateutil.parser.parse(bug["creation_time"])

    ret = False

    for history in reversed(bug["history"]):
        # TODO: Handle changes to product and component.
        # TODO: This code might be removed when https://bugzilla.mozilla.org/show_bug.cgi?id=1513952 is fixed.

        if ret:
            break

        for change in history["changes"]:
            if change is change_to_return:
                ret = True
                break

            field = change["field_name"]

            if field in "component":
                # TODO: Ignore this for now, not so easy to make it work https://bugzilla.mozilla.org/show_bug.cgi?id=1513952.
                continue

            if field == "qa_contact":
                # TODO: Ignore this for now. Example usage in 92144.
                continue

            if field == "cf_fx_iteration":
                # TODO: Ignore this for now. Example usage in 1101478.
                continue

            if field == "cf_crash_signature":
                # TODO: Ignore this for now. Example usage in 1437575.
                continue

            if field == "cf_backlog":
                # TODO: Ignore this for now. Example usage in 1048455.
                continue

            if field == "bug_mentor":
                # TODO: Ignore this for now. Example usage in 1042103.
                continue

            if field == "cf_user_story":
                # TODO: Ignore this for now. Example usage in 1369255.
                # Seems to be broken in Bugzilla.
                continue

            if field == "cf_rank":
                # TODO: Ignore this for now. Example usage in 1475099.
                continue

            if field in ["alias", "restrict_comments"]:
                continue

            if field == "longdescs.isprivate":
                # Ignore for now.
                continue

            if field == "version":
                # TODO: Ignore this for now. Example usage in 1162372 or 1389926.
                continue

            if "attachment_id" in change and field.startswith("attachments"):
                # TODO: Ignore changes to attachments for now.
                continue

            if field == "flagtypes.name":
                if "attachment_id" in change:
                    # https://bugzilla.mozilla.org/show_bug.cgi?id=1516172
                    if bug["id"] == 1_421_395:
                        continue

                    obj = None
                    for attachment in bug["attachments"]:
                        if attachment["id"] == change["attachment_id"]:
                            obj = attachment
                            break

                    if obj is None:
                        assert_or_log(f'Attachment {change["attachment_id"]} not found')
                        continue
                else:
                    obj = bug

                if change["added"]:
                    for to_remove in change["added"].split(", "):
                        # TODO: Skip needinfo/reviews for now, we need a way to match them precisely when there are multiple needinfos/reviews requested.
                        is_question_flag = any(
                            to_remove.startswith(s)
                            for s in [
                                "needinfo",
                                "review",
                                "feedback",
                                "ui-review",
                                "sec-approval",
                                "sec-review",
                                "data-review",
                                "approval-mozilla-",
                            ]
                        )

                        name, status, requestee = parse_flag_change(to_remove)

                        found_flag = None
                        for f in obj["flags"]:
                            if (
                                f["name"] == name
                                and f["status"] == status
                                and (
                                    requestee is None
                                    or (
                                        "requestee" in f and f["requestee"] == requestee
                                    )
                                )
                            ):
                                if (
                                    found_flag is not None
                                    and not is_expected_inconsistent_change_flag(
                                        to_remove, obj["id"]
                                    )
                                    and not is_question_flag
                                ):
                                    flag_text = "{}{}".format(f["name"], f["status"])
                                    if "requestee" in f:
                                        flag_text = "{}{}".format(
                                            flag_text, f["requestee"]
                                        )
                                    assert_or_log(f"{flag_text} found twice!")
                                found_flag = f

                        if found_flag is not None:
                            obj["flags"].remove(found_flag)
                        elif (
                            not is_expected_inconsistent_change_flag(
                                to_remove, obj["id"]
                            )
                            and not is_question_flag
                        ):
                            assert_or_log(
                                f"flag {to_remove} not found, in obj {obj['id']}"
                            )

                if change["removed"]:
                    # Inconsistent review and needinfo flags.
                    if bug["id"] in [785931, 1_342_178]:
                        continue

                    for to_add in change["removed"].split(", "):
                        name, status, requestee = parse_flag_change(to_add)

                        new_flag = {"name": name, "status": status}
                        if requestee is not None:
                            new_flag["requestee"] = requestee

                        obj["flags"].append(new_flag)

                continue

            # We don't support comment tags yet.
            if field == "comment_tag":
                continue

            if field == "comment_revision":
                obj = None
                for comment in bug["comments"]:
                    if comment["id"] == change["comment_id"]:
                        obj = comment
                        break

                if obj is None:
                    if change["comment_id"] != 14096735:
                        assert_or_log(f'Comment {change["comment_id"]} not found')
                    continue

                if obj["count"] != change["comment_count"]:
                    assert_or_log("Wrong comment count")

                # TODO: It should actually be applied on "raw_text".
                # if obj["text"] != change["added"]:
                #     assert_or_log(f"Current value for comment: ({obj['text']}) is different from previous value: ({change['added']}")

                obj["text"] = change["removed"]

                continue

            if change["added"] != "---":
                if field not in bug and not is_expected_inconsistent_field(
                    field, last_product, bug["id"]
                ):
                    assert_or_log(f"{field} is not present")

            if field in bug and isinstance(bug[field], list):
                if change["added"]:
                    for to_remove in change["added"].split(", "):
                        if field in FIELD_TYPES:
                            try:
                                to_remove = FIELD_TYPES[field](to_remove)
                            except Exception:
                                assert_or_log(
                                    f"Exception while transforming {to_remove} from {bug[field]} (field {field})"
                                )

                        if to_remove in bug[field]:
                            bug[field].remove(to_remove)
                        elif not is_expected_inconsistent_change_list_field(
                            field, bug["id"], to_remove
                        ):
                            assert_or_log(
                                f"{to_remove} is not in {bug[field]}, for field {field}"
                            )

                if change["removed"]:
                    for to_add in change["removed"].split(", "):
                        if field in FIELD_TYPES:
                            try:
                                to_add = FIELD_TYPES[field](to_add)
                            except Exception:
                                assert_or_log(
                                    f"Exception while transforming {to_add} from {bug[field]} (field {field})"
                                )
                        bug[field].append(to_add)
            else:
                if field in FIELD_TYPES:
                    try:
                        old_value = FIELD_TYPES[field](change["removed"])
                    except Exception:
                        assert_or_log(
                            f"Exception while transforming {change['removed']} from {bug[field]} (field {field})"
                        )
                    try:
                        new_value = FIELD_TYPES[field](change["added"])
                    except Exception:
                        assert_or_log(
                            f"Exception while transforming {change['added']} from {bug[field]} (field {field})"
                        )
                else:
                    old_value = change["removed"]
                    new_value = change["added"]

                if (
                    field in bug
                    and bug[field] != new_value
                    and not is_expected_inconsistent_change_field(
                        field, bug["id"], new_value, bug[field]
                    )
                ):
                    assert_or_log(
                        f"Current value for field {field}: ({bug[field]}) is different from previous value: ({new_value})"
                    )

                bug[field] = old_value

    if len(bug["comments"]) == 0:
        assert_or_log("There must be at least one comment")
        bug["comments"] = [
            {
                "count": 0,
                "id": 0,
                "text": "",
                "author": bug["creator"],
                "creation_time": bug["creation_time"],
            }
        ]

    # If the first comment is hidden.
    if bug["comments"][0]["count"] != 0:
        bug["comments"].insert(
            0,
            {
                "id": 0,
                "text": "",
                "author": bug["creator"],
                "creation_time": bug["creation_time"],
            },
        )

    bug["comments"] = [
        c
        for c in bug["comments"]
        if dateutil.parser.parse(c["creation_time"]) - relativedelta(seconds=3)
        <= rollback_date
    ]
    bug["attachments"] = [
        a
        for a in bug["attachments"]
        if dateutil.parser.parse(a["creation_time"]) - relativedelta(seconds=3)
        <= rollback_date
    ]

    return bug


def get_inconsistencies(bugs):
    inconsistencies = []

    for bug in bugs:
        try:
            rollback(bug, do_assert=True)
        except Exception as e:
            print(bug["id"])
            print(e)
            inconsistencies.append(bug)

    return inconsistencies


if __name__ == "__main__":
    import argparse

    from tqdm import tqdm

    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", help="Verbose mode", action="store_true")
    args = parser.parse_args()

    for bug in tqdm(bugzilla.get_bugs()):
        if args.verbose:
            print(bug["id"])

        rollback(bug, do_assert=True)
