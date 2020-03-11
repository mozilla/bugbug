# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import hglib
import pytest

from bugbug_http.models import schedule_tests


def test_simple_schedule(patch_resources, mock_hgmo, mock_repo):

    # The repo should be almost empty at first
    repo_dir = mock_repo
    repo = hglib.open(str(repo_dir))
    assert len(repo.log()) == 4
    test_txt = repo_dir / "test.txt"
    assert test_txt.exists()
    assert test_txt.read_text("utf-8") == "Version 3"

    # Scheduling a test on a revision should apply changes in the repo
    assert schedule_tests("mozilla-central", "12345deadbeef") == "OK"

    # Check changes have been applied
    assert len(repo.log()) == 5
    assert test_txt.read_text("utf-8") == "Version 3\nThis is a new line\n"


@pytest.mark.parametrize(
    "branch, revision, result, final_log",
    [
        # patch from autoland based on local parent n°0
        (
            "integration/autoland",
            "normal123",
            "OK",
            ["Target patch", "Parent 123", "Base history 0"],
        ),
        # patch from autoland where parent is not available
        # so the patch is applied on top of tip
        (
            "integration/autoland",
            "orphan123",
            "OK",
            [
                "Orphan 123",
                "Base history 3",
                "Base history 2",
                "Base history 1",
                "Base history 0",
            ],
        ),
        # patch from try based on local parent n°1
        (
            "try",
            "normal456",
            "OK",
            ["Target patch", "Parent 456", "Base history 1", "Base history 0"],
        ),
        # patch from try where parent is not available
        # so the patch is applied on top of tip
        (
            "try",
            "orphan456",
            "OK",
            [
                "Orphan 456",
                "Base history 3",
                "Base history 2",
                "Base history 1",
                "Base history 0",
            ],
        ),
        # bad patch that does not apply
        # The repository is updated to the local base revision
        # even if the patch does not apply afterward
        (
            "try",
            "bad123",
            "NOK",
            ["Base history 2", "Base history 1", "Base history 0"],
        ),
        # patch that only applies after a pull has been done
        (
            "try",
            "needRemote",
            "OK",
            [
                "Depends on remote",
                "Pulled from remote",
                "Base history 3",
                "Base history 2",
                "Base history 1",
                "Base history 0",
            ],
        ),
    ],
)
def test_schedule(
    branch, revision, result, final_log, patch_resources, mock_hgmo, mock_repo
):

    # The repo should only have the base commits
    repo_dir = mock_repo
    repo = hglib.open(str(repo_dir))
    logs = repo.log(follow=True)
    assert len(logs) == 4
    assert [l.desc.decode("utf-8") for l in logs] == [
        "Base history 3",
        "Base history 2",
        "Base history 1",
        "Base history 0",
    ]

    # Schedule tests for parametrized revision
    assert schedule_tests(branch, revision) == result

    # Now check the log has evolved
    assert final_log == [l.desc.decode("utf-8") for l in repo.log(follow=True)]
