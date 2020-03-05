# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import pytest

from bugbug_http.models import schedule_tests


def test_simple_schedule(patch_resources, mock_hgmo, mock_repo):

    # The repo should be almost empty at first
    repo_dir, repo = mock_repo
    assert len(repo.log()) == 4
    test_txt = repo_dir / "test.txt"
    assert test_txt.exists()
    assert test_txt.read_text("utf-8") == "Version 3"

    # Scheduling a test on a revision should apply changes in the repo
    schedule_tests("mozilla-central", "12345deadbeef")

    # Check changes have been applied
    assert len(repo.log()) == 5
    assert test_txt.read_text("utf-8") == "Version 3\nThis is a new line\n"


@pytest.mark.parametrize(
    "branch, revision, final_log",
    [
        # patch from autoland based on local parent n°0
        ("autoland", "normal123", ["Target patch", "Parent 123", "Base history 0"]),
        # patch from autoland where parent is not available
        # so the patch is applied on top of tip
        (
            "autoland",
            "orphan123",
            [
                "Orphan 123",
                "Base history 3",
                "Base history 2",
                "Base history 1",
                "Base history 0",
            ],
        ),
        # patch from try
        # ("try", "normal567"),
        # patch from try where parent is not available
        # ("try", "orpahn567"),
    ],
)
def test_schedule(branch, revision, final_log, patch_resources, mock_hgmo, mock_repo):

    # The repo should only have the base commits
    repo_dir, repo = mock_repo
    logs = repo.log(follow=True)
    assert len(logs) == 4
    assert [l.desc.decode("utf-8") for l in logs] == [
        "Base history 3",
        "Base history 2",
        "Base history 1",
        "Base history 0",
    ]

    # Schedule tests for parametrized revision
    schedule_tests(branch, revision)

    # Now check the log has evolved
    assert final_log == [l.desc.decode("utf-8") for l in repo.log(follow=True)]
