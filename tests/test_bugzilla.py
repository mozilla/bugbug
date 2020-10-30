# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from bugbug import bugzilla


def test_get_bugs():
    all_bugs = {int(bug["id"]) for bug in bugzilla.get_bugs(include_invalid=True)}
    legitimate_bugs = {int(bug["id"]) for bug in bugzilla.get_bugs()}

    assert 1541482 in all_bugs
    assert 1541482 not in legitimate_bugs

    assert 1559674 in all_bugs
    assert 1559674 not in legitimate_bugs

    assert 1549207 in all_bugs
    assert 1549207 not in legitimate_bugs

    assert 1572747 in all_bugs
    assert 1572747 in legitimate_bugs


def test_get_fixed_versions():
    assert (
        bugzilla.get_fixed_versions(
            {
                "target_milestone": "mozilla81",
                "cf_tracking_firefox83": "blocking",
                "cf_status_firefox82": "fixed",
                "cf_status_firefox81": "unaffected",
            }
        )
        == [81, 82]
    )

    assert (
        bugzilla.get_fixed_versions(
            {
                "target_milestone": "mozilla82",
                "cf_tracking_firefox82": "---",
                "cf_status_firefox82": "fixed",
                "cf_status_firefox83": "fixed",
            }
        )
        == [82, 83]
    )

    assert (
        bugzilla.get_fixed_versions(
            {
                "target_milestone": "mozilla82",
            }
        )
        == [82]
    )

    assert (
        bugzilla.get_fixed_versions(
            {
                "target_milestone": "82 Branch",
            }
        )
        == [82]
    )

    assert (
        bugzilla.get_fixed_versions(
            {
                "target_milestone": "Firefox 82",
            }
        )
        == [82]
    )
