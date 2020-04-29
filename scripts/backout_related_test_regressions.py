# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
import argparse
import json

from mozci.push import Push
from tqdm import tqdm

from bugbug import db, repository


def go() -> None:
    assert db.download(repository.COMMITS_DB)

    backouts = []
    backedouts = []
    for commit in repository.get_commits(include_backouts=True):
        if commit["backedoutby"]:
            backouts.append(commit["node"])
        if commit["backsout"]:
            backedouts += commit["backsout"]

    backouts = backouts[-100:]
    backedouts = backedouts[-100:]

    likely_label_count = 0
    possible_label_count = 0
    likely_group_count = 0
    possible_group_count = 0

    backout_regressions = {}

    for backout in tqdm(backouts):
        p = Push(backout)

        label_regressions = p.get_regressions("label")
        likely_label_count += len(p.get_likely_regressions("label"))
        possible_label_count += len(p.get_possible_regressions("label"))

        group_regressions = p.get_regressions("group")
        likely_group_count += len(p.get_likely_regressions("label"))
        possible_group_count += len(p.get_possible_regressions("label"))

        if len(label_regressions) > 0 or len(group_regressions) > 0:
            backout_regressions[backout] = {
                "label": label_regressions,
                "group": group_regressions,
            }

    print(f"Likely labels for backouts: {likely_label_count}")
    print(f"Likely groups for backouts: {likely_group_count}")
    print(f"Possible labels for backouts: {possible_label_count}")
    print(f"Possible groups for backouts: {possible_group_count}")

    backedout_regressions = {}

    for backedout in tqdm(backedouts):
        p = Push(backedout)

        label_regressions = p.get_regressions("label")
        group_regressions = p.get_regressions("group")

        if (
            len(p.get_likely_regressions("label")) == 0
            or len(p.get_likely_regressions("group")) == 0
        ):
            backedout_regressions[backedout] = {
                "label": label_regressions,
                "group": group_regressions,
            }

    with open("backout_regressions.json", "w") as f:
        json.dump(backout_regressions, f)

    with open("backedout_regressions.json", "w") as f:
        json.dump(backedout_regressions, f)


def main() -> None:
    description = (
        "Find likely and possible test regressions of backouts and backed-out commits"
    )
    parser = argparse.ArgumentParser(description=description)
    parser.parse_args()

    go()


if __name__ == "__main__":
    main()
