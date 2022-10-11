# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse

import dateutil.parser

from bugbug import bugzilla

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
        round(
            bugzilla.calculate_maintenance_effectiveness_indicator(
                args.team,
                dateutil.parser.parse(args.start_date),
                dateutil.parser.parse(args.end_date),
                args.components,
            ),
            2,
        )
    )
