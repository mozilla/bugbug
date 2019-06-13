# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import csv
import os
import random

from bugbug import bugzilla
from bugbug.models import load_model

parser = argparse.ArgumentParser()
parser.add_argument(
    "--goal",
    help="Goal of the labeler",
    choices=["str", "regressionrange"],
    default="str",
)
args = parser.parse_args()

if args.goal == "str":
    model = load_model("bug")
elif args.goal == "regressionrange":
    model = load_model("regression")

file_path = os.path.join("bugbug", "labels", f"{args.goal}.csv")

with open(file_path, "r") as f:
    reader = csv.reader(f)
    next(reader)
    labeled_comments = [(int(r[0]), int(r[1]), r[2]) for r in reader]

already_done = set((c[0], c[1]) for c in labeled_comments)

bugs = []
for bug in bugzilla.get_bugs():
    # For the str and regressionrange problems, we don't care about test failures,
    if (
        "intermittent-failure" in bug["keywords"]
        or "stockwell" in bug["whiteboard"]
        or "permafail" in bug["summary"].lower()
    ):
        continue

    # bugs filed from Socorro,
    if (
        "this bug was filed from the socorro interface"
        in bug["comments"][0]["text"].lower()
    ):
        continue

    # and fuzzing bugs.
    if "fuzzing" in bug["comments"][0]["text"].lower():
        continue

    bugs.append(bug)

random.shuffle(bugs)

for bug in bugs:
    # Only show bugs that are really bugs/regressions for labeling.
    c = model.classify(bug)
    if c != 1:
        continue

    v = None

    for i, comment in enumerate(bug["comments"]):
        if (bug["id"], i) in already_done:
            continue

        os.system("clear")
        print(f'Bug {bug["id"]} - {bug["summary"]}')
        print(f"Comment {i}")
        print(comment["text"])

        if args.goal == "str":
            print(
                "\nY for comment containing STR, N for comment not containing STR, K to skip, E to exit"
            )
        elif args.goal == "regressionrange":
            print(
                "\nY for comment containing regression range, N for comment not containing regression range, K to skip, E to exit"
            )
        v = input()

        if v in ["e", "k"]:
            break

        if v in ["y", "n"]:
            labeled_comments.append((bug["id"], i, v))

    if v not in ["e", "k"]:
        with open(file_path, "w") as f:
            writer = csv.writer(f)
            writer.writerow(["bug_id", "comment_num", f"has_{args.goal}"])
            writer.writerows(sorted(labeled_comments))

        print("\nE to exit, anything else to continue")
        v = input()

    if v == "e":
        break
