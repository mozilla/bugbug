# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import math
from logging import INFO, basicConfig, getLogger

import dateutil.parser

from bugbug import bugzilla
from bugbug.utils import get_secret

basicConfig(level=INFO)
logger = getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("teams", help="Bugzilla team", type=str, nargs="+")
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

    # Try to use a Bugzilla API key if available.
    try:
        bugzilla.set_token(get_secret("BUGZILLA_TOKEN"))
    except ValueError:
        logger.info(
            "If you want to include security bugs too, please set the BUGBUG_BUGZILLA_TOKEN environment variable to your Bugzilla API key."
        )

    result = bugzilla.calculate_maintenance_effectiveness_indicator(
        args.teams,
        dateutil.parser.parse(args.start_date),
        dateutil.parser.parse(args.end_date),
        args.components,
    )

    for factor, value in result["stats"].items():
        print("%s: %d" % (factor, round(value, 2) if value != math.inf else value))

    for query, link in result["queries"].items():
        print(f"{query}: {link}")


if __name__ == "__main__":
    main()
