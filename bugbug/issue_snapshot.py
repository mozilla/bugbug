# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.


def rollback(issue, when=None):
    assert when is None, "Rollback to a specific point in history is not supported yet."

    if issue["events"]:
        for event in issue["events"]:
            # Extract original title that issue got at the moment of creation
            if (
                event["event"] == "renamed"
                and event["rename"]["from"] != "In the moderation queue."
                and event["rename"]["from"] != "Issue closed."
            ):
                issue["title"] = event["rename"]["from"]

    return issue
