# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import csv

import requests

params = {
    "columnlist": "bug_type",
    "order": "bug_id",
    "j_top": "OR",
    "f1": "bug_type",
    "o1": "changedafter",
    "v1": "1970-01-01",
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

reader = csv.reader(r.text.splitlines())
next(reader)

with open("bugbug/labels/defect_enhancement_task_h.csv", "w") as f:
    writer = csv.writer(f)
    writer.writerow(headers)
    writer.writerows(reader)
