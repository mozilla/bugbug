# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import json

import hglib
import pytest

from bugbug_http import models


@pytest.mark.parametrize(
    "labels_to_choose, groups_to_choose",
    [
        # one from label, one from group
        (["test-label1"], ["test-group2"],),
        # one from label, none from group
        (["test-label1"], [],),
        # none from label, one from group
        ([], ["test-group1"],),
        # two from label, one from group
        (["test-label1", "test-label2"], ["test-group2"],),
    ],
)
def test_simple_schedule(
    labels_to_choose,
    groups_to_choose,
    monkeypatch,
    mock_hgmo,
    mock_repo,
    mock_component_taskcluster_artifact,
    mock_schedule_tests_classify,
):
    # The repo should be almost empty at first
    repo_dir, _ = mock_repo
    repo = hglib.open(str(repo_dir))
    assert len(repo.log()) == 4
    test_txt = repo_dir / "test.txt"
    assert test_txt.exists()
    assert test_txt.read_text("utf-8") == "Version 3"

    mock_schedule_tests_classify(labels_to_choose, groups_to_choose)

    # Scheduling a test on a revision should apply changes in the repo
    assert models.schedule_tests("mozilla-central", "12345deadbeef") == "OK"

    # Check changes have been applied
    assert len(repo.log()) == 5
    assert test_txt.read_text("utf-8") == "Version 3\nThis is a new line\n"

    # Assert the test selection result is stored in Redis.
    assert json.loads(
        models.redis.get(
            "bugbug:job_result:schedule_tests:mozilla-central_12345deadbeef"
        )
    ) == {"tasks": labels_to_choose, "groups": groups_to_choose,}


@pytest.mark.parametrize(
    "branch, revision, result, final_log",
    [
        # patch from autoland based on local parent n°0
        (
            "integration/autoland",
            "normal123",
            "OK",
            ["Bug 123 - Target patch", "Bug 123 - Parent 123", "Base history 0"],
        ),
        # patch from autoland where parent is not available
        # so the patch is applied on top of tip
        (
            "integration/autoland",
            "orphan123",
            "OK",
            [
                "Bug 123 - Orphan 123",
                "Base history 3",
                "Base history 2",
                "Base history 1",
                "Base history 0",
            ],
        ),
        # patch on autoland that only applies after a pull has been done
        (
            "integration/autoland",
            "needRemote",
            "OK",
            [
                "Bug 123 - On top of remote + local",
                "Bug 123 - Based on remote",
                "Pulled from remote",
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
            [
                "Bug 123 - Target patch",
                "Bug 123 - Parent 456",
                "Base history 1",
                "Base history 0",
            ],
        ),
        # patch from try where parent is not available
        # so the patch is applied on top of tip
        (
            "try",
            "orphan456",
            "OK",
            [
                "Bug 123 - Orphan 456",
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
        # patch on try that only applies after a pull has been done
        (
            "try",
            "needRemote",
            "OK",
            [
                "Bug 123 - Depends on remote",
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
    branch,
    revision,
    result,
    final_log,
    patch_resources,
    mock_hgmo,
    mock_repo,
    mock_component_taskcluster_artifact,
    mock_schedule_tests_classify,
):

    # The repo should only have the base commits
    repo_dir, _ = mock_repo
    repo = hglib.open(str(repo_dir))
    logs = repo.log(follow=True)
    assert len(logs) == 4
    assert [l.desc.decode("utf-8") for l in logs] == [
        "Base history 3",
        "Base history 2",
        "Base history 1",
        "Base history 0",
    ]

    mock_schedule_tests_classify(["test-label1"], ["test-group2"])

    # Schedule tests for parametrized revision
    assert models.schedule_tests(branch, revision) == result

    # Now check the log has evolved
    assert final_log == [l.desc.decode("utf-8") for l in repo.log(follow=True)]

    if result == "OK":
        # Assert the test selection result is stored in Redis.
        assert json.loads(
            models.redis.get(f"bugbug:job_result:schedule_tests:{branch}_{revision}")
        ) == {"tasks": ["test-label1"], "groups": ["test-group2"],}
