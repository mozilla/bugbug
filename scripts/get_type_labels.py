# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import csv
import sys

import requests


def parse_args(args):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--types",
        help="Types to retrieve",
        default=["defect", "enhancement", "task"],
        nargs="*",
    )
    return parser.parse_args(args)


def main(args):
    params = {
        "columnlist": "bug_type",
        "order": "bug_id",
        "j_top": "OR",
        "f1": "bug_type",
        "o1": "everchanged",
        "f2": "OP",
        "f3": "bug_type",
        "o3": "anyexact",
        "v3": "task,enhancement",
        "f4": "bug_id",
        "o4": "greaterthan",
        "v4": 1540807,
        "f5": "CP",
        "ctype": "csv",
    }

    r = requests.get("https://bugzilla.mozilla.org/buglist.cgi", params=params)
    r.raise_for_status()

    with open("bugbug/labels/defect_enhancement_task_h.csv", "r") as f:
        reader = csv.reader(f)
        headers = next(reader)
        bug_type_map = {int(row[0]): row[1] for row in reader}

    # We add to our csv both labels that were changed, and labels that are in
    # the list of requested types.
    reader = csv.reader(r.text.splitlines())
    next(reader)
    for row in reader:
        if int(row[0]) in bug_type_map or row[1] in args.types:
            bug_type_map[int(row[0])] = row[1]

    with open("bugbug/labels/defect_enhancement_task_h.csv", "w") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(sorted(bug_type_map.items()))


if __name__ == "__main__":
    main(parse_args(sys.argv[1:]))
