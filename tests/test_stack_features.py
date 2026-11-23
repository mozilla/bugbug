# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from bugbug import commit_features
from bugbug.commit_features import (
    BugAgeAtLanding,
    BugLandingHistory,
    BugProduct,
    StackInfoIndex,
    StackPartPosition,
    StackSize,
    StackSpread,
    StackTests,
)


def make_commit(node, bug_id, pushdate, desc="Bug - fix", backedoutby="", **kwargs):
    commit = {
        "node": node,
        "bug_id": bug_id,
        "pushdate": pushdate,
        "desc": desc,
        "backedoutby": backedoutby,
        "source_code_added": 10,
        "source_code_deleted": 2,
        "test_added": 0,
        "source_code_files_modified_num": 1,
        "test_files_modified_num": 0,
        "functions": {"a.cpp": [["f", 1, 2]]},
        "components": ["Core::DOM"],
        "directories": ["dom", "dom/base"],
        "types": ["C/C++"],
    }
    commit.update(kwargs)
    return commit


def test_stack_and_history():
    commits = [
        # An earlier landing of bug 1, backed out.
        make_commit("a", 1, "2024-01-01 10:00:00", backedoutby="z"),
        # The reland, as a stack of two patches.
        make_commit(
            "b",
            1,
            "2024-01-05 10:00:00",
            desc="Bug 1 - Part 1: refactor",
            test_added=5,
            test_files_modified_num=1,
        ),
        make_commit(
            "c",
            1,
            "2024-01-05 10:00:00",
            desc="Bug 1 - Part 2: fix",
            source_code_added=30,
            components=["Core::Layout"],
            directories=["layout", "layout/base"],
        ),
        # Another bug.
        make_commit("d", 2, "2024-01-05 10:00:00"),
    ]
    index = StackInfoIndex(commits)
    c = commits[2]
    index.attach(c)

    size = StackSize()(c)
    assert size["Stack commits"] == 2
    assert size["Stack total source_code_added"] == 40
    assert size["Stack max source_code_added"] == 30
    assert size["Stack total functions_touched_num"] == 2

    spread = StackSpread()(c)
    assert spread["Stack components"] == 2
    assert spread["Stack top-level directories"] == 2
    assert spread["Stack directories"] == 4

    tests = StackTests()(c)
    assert tests["Stack has test changes"] is True
    assert tests["Stack share of commits with tests"] == 0.5
    assert tests["Stack test/code lines ratio"] == 5 / 41

    pos = StackPartPosition()(c)
    assert pos == {
        "Has part number": True,
        "Part number": 2,
        "Is last part": True,
        "Part relative position": 1.0,
    }

    history = BugLandingHistory()(c)
    assert history["Bug earlier pushes"] == 1
    assert history["Bug earlier backed-out commits"] == 1
    assert history["Bug is a reland"] is True
    assert history["Bug days since first landing"] == 4
    assert history["Bug days since last landing"] == 4

    # The first landing has no history.
    index.attach(commits[0])
    history = BugLandingHistory()(commits[0])
    assert history["Bug earlier pushes"] == 0
    assert history["Bug is a reland"] is False
    assert history["Bug days since first landing"] == -1


def test_explicit_stack_and_unindexed_commits():
    index = StackInfoIndex([make_commit("a", 1, "2024-01-01 10:00:00")], stacks=False)

    # A patch under review, with its stack.
    reviewed = make_commit("x", 1, "2024-02-01 10:00:00")
    stack = [make_commit("w", 1, "2024-02-01 10:00:00"), reviewed]
    index.attach(reviewed, stack)
    assert StackSize()(reviewed)["Stack commits"] == 2
    assert BugLandingHistory()(reviewed)["Bug earlier pushes"] == 1

    # A commit which isn't indexed, with another commit of its push.
    other = make_commit("y", 1, "2024-03-01 10:00:00")
    new = make_commit("v", 1, "2024-03-01 10:00:00")
    index.attach(new, other_commits=[other, new])
    assert StackSize()(new)["Stack commits"] == 2

    # Commits without a bug are a stack of their own.
    nobug = make_commit("n", None, "2024-03-01 10:00:00")
    index.attach(nobug)
    assert StackSize()(nobug)["Stack commits"] == 1
    assert BugLandingHistory()(nobug)["Bug earlier pushes"] == 0


def test_no_stack_info():
    commit = make_commit("a", 1, "2024-01-01 10:00:00")
    for extractor in (
        StackSize(),
        StackSpread(),
        StackTests(),
        StackPartPosition(),
        BugLandingHistory(),
        BugProduct(),
        BugAgeAtLanding(),
    ):
        assert extractor(commit) is None


def test_bug_features():
    commit = make_commit("a", 1, "2024-01-11 10:00:00")
    commit["bug"] = {"product": "Core", "creation_time": "2024-01-01T09:00:00Z"}
    assert BugProduct()(commit) == "Core"
    assert BugAgeAtLanding()(commit) == 10

    commit["bug"] = {}
    assert BugProduct()(commit) is None
    assert BugAgeAtLanding()(commit) is None


def test_part_number():
    assert commit_features._part_number("Bug 1 - part 3: do things") == 3
    assert commit_features._part_number("Bug 1 - Part3 - do things") == 3
    assert commit_features._part_number("Bug 1 - do things\n\npart 2 is later") is None
