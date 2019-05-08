# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import dateutil.parser
from dateutil.relativedelta import relativedelta

from bugbug import bugzilla


def bool_str(val):
    assert val in ["", "0", "1"], f"Unexpected boolean value: '{val}'"

    return True if val == "1" else False


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
        "csec-dos": "csectype-dos",
        "csec-oom": "csectype-oom",
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
        "Seamonkey": "Mozilla Application Suite",
        "SeaMonkey": "Mozilla Application Suite",
    }

    return mapping[product] if product in mapping else product


def target_milestone(target_milestone):
    if target_milestone.startswith("Seamonkey"):
        return target_milestone.lower()

    mapping = {"6.2.2": "6.2.2.1"}

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
    "product": product,
    "target_milestone": target_milestone,
    "cf_due_date": null_str,
}


def is_email(val):
    return isinstance(val, str) and "@" in val


def parse_flag_change(change):
    parts = change.split("(")
    assert len(parts) == 1 or len(parts) == 2, f"Too many parts for {change}"
    name_and_status = parts[0]
    name = name_and_status[:-1]
    status = name_and_status[-1]
    assert status in ["?", "+", "-"], f"unexpected status: {status}"
    requestee = None if len(parts) != 2 else parts[1][:-1]
    return name, status, requestee


def is_expected_inconsistent_field(field, last_product, bug_id):
    # TODO: Remove the Graveyard case when https://bugzilla.mozilla.org/show_bug.cgi?id=1541926 is fixed.
    return (
        (field.startswith("cf_") and last_product == "Firefox for Android Graveyard")
        or (field == "cf_tracking_firefox59" and bug_id in [1_443_367, 1_443_630])
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
            ]
        )
        or (field in ["cf_has_str", "cf_has_regression_range"] and bug_id == 1_440_338)
        or (field == "cf_has_regression_range" and bug_id == 1542185)
        or (
            field == "cf_has_str" and bug_id == 1462571
        )  # TODO: Remove when https://bugzilla.mozilla.org/show_bug.cgi?id=1550104 is fixed
    )


def is_expected_inconsistent_change_field(field, bug_id, new_value):
    # The 'enhancement' severity has been removed, but it doesn't show up in the history.
    # See https://bugzilla.mozilla.org/show_bug.cgi?id=1541362.
    return (
        (field in ["status", "resolution", "cf_last_resolved"] and bug_id == 1_312_722)
        or (field == "cf_last_resolved" and bug_id == 1_321_567)
        or (field == "url" and bug_id == 740_223)
        or (field == "severity" and new_value == "enhancement")
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
            ]
        )
        or (field == "cf_tracking_firefox60" and bug_id in [1_375_913, 1_439_875])
        or (field == "priority" and bug_id == 1_337_747)
        or (
            field == "type" and bug_id == 1540796
        )  # TODO: Remove once https://bugzilla.mozilla.org/show_bug.cgi?id=1550120 is fixed.
        or (
            field == "cf_last_resolved" and bug_id == 1540998
        )  # TODO: Remove once https://bugzilla.mozilla.org/show_bug.cgi?id=1550128 is fixed.
        or (
            field == "type" and bug_id == 1257155
        )  # TODO: Remove once https://bugzilla.mozilla.org/show_bug.cgi?id=1550129 is fixed.
    )


def rollback(bug, when, verbose=True, all_inconsistencies=False):
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
        pass

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
                    assert obj is not None
                else:
                    obj = bug

                if change["added"]:
                    for to_remove in change["added"].split(", "):
                        if to_remove.startswith("approval-comm-beta"):
                            # Skip this for now.
                            continue

                        # These flags have been removed.
                        if to_remove in ["platform-rel?", "blocking0.3-"]:
                            continue

                        if any(
                            to_remove.startswith(s)
                            for s in [
                                "needinfo",
                                "review",
                                "feedback",
                                "ui-review",
                                "sec-approval",
                                "sec-review",
                            ]
                        ):
                            # TODO: Skip needinfo/reviews for now, we need a way to match them precisely when there are multiple needinfos/reviews requested.
                            continue

                        name, status, requestee = parse_flag_change(to_remove)

                        found_flag = None
                        for f in obj["flags"]:
                            if (
                                f["name"] == name
                                and f["status"] == status
                                and (requestee is None or f["requestee"] == requestee)
                            ):
                                assert (
                                    found_flag is None
                                ), f'{f["name"]}{f["status"]}{f["requestee"]} found twice!'
                                found_flag = f

                        # TODO: always assert here, once https://bugzilla.mozilla.org/show_bug.cgi?id=1514415 is fixed.
                        if (
                            obj["id"] not in [1_052_536, 1_201_115, 1_213_517, 794_863]
                            and not (
                                to_remove == "in-testsuite+"
                                and obj["id"]
                                in [
                                    1_318_438,
                                    1_312_852,
                                    1_332_255,
                                    1_344_690,
                                    1_362_387,
                                    1_380_306,
                                ]
                            )
                            and not (
                                to_remove == "in-testsuite-"
                                and bug["id"] in [1_321_444, 1_342_431, 1_370_129]
                            )
                            and not (
                                to_remove == "approval-comm-esr52?"
                                and bug["id"] == 1_352_850
                            )
                            and not (
                                to_remove == "checkin+"
                                and bug["id"]
                                in [
                                    1_308_868,
                                    1_357_808,
                                    1_361_361,
                                    1_365_763,
                                    1_328_454,
                                ]
                            )
                            and not (to_remove == "checkin-" and bug["id"] == 1_412_952)
                            and not (
                                to_remove == "webcompat?"
                                and obj["id"] in [1_360_579, 1_364_598]
                            )
                            and not (
                                to_remove == "qe-verify-"
                                and bug["id"]
                                in [
                                    1_322_685,
                                    1_336_510,
                                    1_363_358,
                                    1_370_506,
                                    1_374_024,
                                    1_377_911,
                                    1_393_848,
                                    1_396_334,
                                    1_398_874,
                                    1_419_371,
                                ]
                            )
                        ):
                            assert (
                                found_flag is not None
                            ), f'flag {to_remove} not found in {bug["id"]}'
                        if found_flag is not None:
                            obj["flags"].remove(found_flag)

                if change["removed"]:
                    # Inconsistent review flag.
                    if bug["id"] == 1_342_178:
                        continue

                    for to_add in change["removed"].split(", "):
                        name, status, requestee = parse_flag_change(to_add)

                        new_flag = {"name": name, "status": status}
                        if requestee is not None:
                            new_flag["requestee"] = requestee

                        obj["flags"].append(new_flag)

                continue

            if change["added"] != "---":
                if field not in bug:
                    if not all_inconsistencies and is_expected_inconsistent_field(
                        field, last_product, bug["id"]
                    ):
                        if verbose:
                            print(f'{field} is not in bug {bug["id"]}')
                    else:
                        assert False, f'{field} is not in bug {bug["id"]}'

            if field in bug and isinstance(bug[field], list):
                if change["added"]:
                    if field == "see_also" and change["added"].endswith(", "):
                        change["added"] = change["added"][:-2]

                    for to_remove in change["added"].split(", "):
                        if field in FIELD_TYPES:
                            to_remove = FIELD_TYPES[field](to_remove)

                        if is_email(to_remove):
                            # TODO: Users can change their email, try with all emails from a mapping file.
                            continue

                        if field == "keywords" and to_remove in [
                            "checkin-needed",
                            "#relman/triage/defer-to-group",
                            "conduit-needs-discussion",
                        ]:
                            # TODO: https://bugzilla.mozilla.org/show_bug.cgi?id=1513981.
                            if to_remove in bug[field]:
                                bug[field].remove(to_remove)
                            continue

                        # These keywords don't exist anymore.
                        if field == "keywords" and to_remove in [
                            "patch",
                            "nsbeta1",
                            "mozilla1.1",
                            "mozilla1.0",
                            "4xp",
                            "sec-review-complete",
                        ]:
                            assert to_remove not in bug[field]
                            continue

                        assert (
                            to_remove in bug[field]
                        ), f'{to_remove} is not in {bug[field]}, for field {field} of {bug["id"]}'
                        bug[field].remove(to_remove)

                if change["removed"]:
                    for to_add in change["removed"].split(", "):
                        if field in FIELD_TYPES:
                            to_add = FIELD_TYPES[field](to_add)
                        bug[field].append(to_add)
            else:
                if field in FIELD_TYPES:
                    old_value = FIELD_TYPES[field](change["removed"])
                    new_value = FIELD_TYPES[field](change["added"])
                else:
                    old_value = change["removed"]
                    new_value = change["added"]

                # TODO: Users can change their email, try with all emails from a mapping file.
                if field in bug and not is_email(bug[field]):
                    if bug[field] != new_value:
                        if (
                            not all_inconsistencies
                            and is_expected_inconsistent_change_field(
                                field, bug["id"], new_value
                            )
                        ):
                            # This case is too common, let's not print anything.
                            if not (field == "severity" and new_value == "enhancement"):
                                print(
                                    f'Current value for field {field} of {bug["id"]}:\n{bug[field]}\nis different from previous value:\n{new_value}'
                                )
                        else:
                            assert (
                                False
                            ), f'Current value for field {field} of {bug["id"]}:\n{bug[field]}\nis different from previous value:\n{new_value}'

                bug[field] = old_value

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

    assert (
        len(bug["comments"]) >= 1
    ), f"There must be at least one comment in bug {bug['id']}"

    return bug


def get_inconsistencies(find_all=False):
    inconsistencies = []

    for bug in bugzilla.get_bugs():
        try:
            rollback(bug, None, False, find_all)
        except Exception as e:
            print(bug["id"])
            print(e)
            inconsistencies.append(bug["id"])

    return inconsistencies


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", help="Verbose mode", action="store_true")
    args = parser.parse_args()

    for i, bug in enumerate(bugzilla.get_bugs()):
        if args.verbose:
            print(bug["id"])
            print(i)
        rollback(bug, None, False)
