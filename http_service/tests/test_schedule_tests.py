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
