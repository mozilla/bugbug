# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import json

import hglib
import pytest

from bugbug_http import models


@pytest.mark.parametrize(
    "labels_to_choose, groups_to_choose, reduced_labels",
    [
        # one from label, one from group
        ({"test-label1": 0.9}, {"test-group2": 0.9}, {"test-label1": 0.9}),
        # one from label, none from group
        ({"test-label1": 0.9}, {"test-group2": 0.9}, {"test-label1": 0.9}),
        # none from label, one from group
        ({}, {"test-group1": 0.9}, {}),
        # two from label, one from group
        (
            {"test-label1": 0.9, "test-label2": 0.4},
            {"test-group2": 0.9},
            {"test-label1": 0.9},
        ),
        # two redundant from label, one from group
        (
            {"test-linux": 0.9, "test-windows": 0.8},
            {"test-group1": 0.9},
            {"test-linux": 0.9},
        ),
    ],
)
def test_simple_schedule(
    labels_to_choose,
    groups_to_choose,
    reduced_labels,
    monkeypatch,
    mock_hgmo,
    mock_repo,
    mock_component_taskcluster_artifact,
    mock_schedule_tests_classify,
):
    # The repo should be almost empty at first
    repo_dir, remote_repo_dir = mock_repo
    with hglib.open(str(repo_dir)) as hg:
        logs = hg.log()
        assert len(logs) == 4
        assert [l.desc.decode("utf-8") for l in logs] == [
            "Base history 3",
            "Base history 2",
            "Base history 1",
            "Base history 0",
        ]
    with hglib.open(str(remote_repo_dir)) as hg:
        rev = hg.log()[-1].node.decode("ascii")[:12]

    mock_schedule_tests_classify(labels_to_choose, groups_to_choose)

    # Scheduling a test on a revision should apply changes in the repo
    assert models.schedule_tests("mozilla-central", rev) == "OK"

    # Check changes have been applied
    with hglib.open(str(repo_dir)) as hg:
        assert len(hg.log()) == 5
        assert [l.desc.decode("utf-8") for l in hg.log()] == [
            "Bug 1 - Pulled from remote",
            "Base history 3",
            "Base history 2",
            "Base history 1",
            "Base history 0",
        ]

    # Assert the test selection result is stored in Redis.
    assert json.loads(
        models.redis.get(f"bugbug:job_result:schedule_tests:mozilla-central_{rev}")
    ) == {
        "tasks": labels_to_choose,
        "groups": groups_to_choose,
        "reduced_tasks": reduced_labels,
    }


@pytest.mark.parametrize(
    "branch, revision, result, final_log",
    [
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
                "Bug 1 - Pulled from remote",
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

    mock_schedule_tests_classify({"test-label1": 0.9}, {"test-group2": 0.9})

    # Schedule tests for parametrized revision
    assert models.schedule_tests(branch, revision) == result

    # Now check the log has evolved
    assert final_log == [l.desc.decode("utf-8") for l in repo.log(follow=True)]

    if result == "OK":
        # Assert the test selection result is stored in Redis.
        assert json.loads(
            models.redis.get(f"bugbug:job_result:schedule_tests:{branch}_{revision}")
        ) == {
            "tasks": {"test-label1": 0.9},
            "groups": {"test-group2": 0.9},
            "reduced_tasks": {"test-label1": 0.9},
        }
