# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse

from bugbug import bugzilla, utils


def calculate_mainentance_effectiveness_metric(team, components, from_date, to_date):
    data: dict[str, dict[str, int]] = {
        "opened": {},
        "closed": {},
    }

    for severity in bugzilla.MAINTENANCE_EFFECTIVENESS_SEVERITY_WEIGHTS.keys():
        params = {
            "count_only": 1,
            "type": "defect",
            "team_name": team,
            "chfieldfrom": from_date,
            "chfieldto": to_date,
        }

        if severity != "--":
            params["bug_severity"] = severity

        if components is not None:
            params["component"] = components

        for query_type in ("opened", "closed"):
            if query_type == "opened":
                params["chfield"] = "[Bug creation]"
            elif query_type == "closed":
                params.update(
                    {
                        "chfield": "cf_last_resolved",
                        "f1": "resolution",
                        "o1": "notequals",
                        "v1": "---",
                    }
                )

            r = utils.get_session("bugzilla").get(
                "https://bugzilla.mozilla.org/rest/bug",
                params=params,
                headers={"User-Agent": "bugbug"},
            )
            r.raise_for_status()

            data[query_type][severity] = r.json()["bug_count"]

    for query_type in ("opened", "closed"):
        data[query_type]["--"] = data[query_type]["--"] - sum(
            data[query_type][s]
            for s in bugzilla.MAINTENANCE_EFFECTIVENESS_SEVERITY_WEIGHTS.keys()
            if s != "--"
        )

        # Apply weights.
        for (
            severity,
            weight,
        ) in bugzilla.MAINTENANCE_EFFECTIVENESS_SEVERITY_WEIGHTS.items():
            data[query_type][severity] *= weight

    print(data)

    return round(
        (1 + sum(data["closed"].values())) / (1 + sum(data["opened"].values())), 2
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("team", help="Bugzilla team", type=str)
    parser.add_argument(
        "start_date",
        help="Start date of the period (YYYY-MM-DD)",
        type=str,
    )
    parser.add_argument(
        "end_date",
        help="End date of the period (YYYY-MM-DD)",
        type=str,
    )
    parser.add_argument(
        "--components",
        help="Bugzilla components",
        type=str,
        nargs="*",
    )

    args = parser.parse_args()

    print(
        calculate_mainentance_effectiveness_metric(
            args.team, args.components, args.start_date, args.end_date
        )
    )
