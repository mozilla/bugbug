# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import json
from typing import Callable, Dict, List, Tuple

import hglib
import pytest

from bugbug_http import models


@pytest.mark.parametrize(
    "labels_to_choose, groups_to_choose, reduced_labels, config_groups",
    [
        # one from label, one from group
        (
            {"test-linux1804-64-opt-label1": 0.9},
            {"test-group2": 0.9},
            {"test-linux1804-64-opt-label1": 0.9},
            {"test-group2": ["test-linux1804-64/opt"]},
        ),
        # one from label, none from group
        (
            {"test-linux1804-64-opt-label1": 0.9},
            {"test-group2": 0.9},
            {"test-linux1804-64-opt-label1": 0.9},
            {"test-group2": ["test-linux1804-64/opt"]},
        ),
        # none from label, one from group
        (
            {},
            {"test-group1": 0.9},
            {},
            {"test-group1": ["test-linux1804-64/opt", "test-windows10/debug"]},
        ),
        # two from label, one from group
        (
            {"test-linux1804-64-opt-label1": 0.9, "test-linux1804-64-opt-label2": 0.5},
            {"test-group2": 0.9},
            {"test-linux1804-64-opt-label1": 0.9},
            {"test-group2": ["test-linux1804-64/opt"]},
        ),
        # two redundant from label, one from group
        (
            {"test-linux1804-64/opt": 0.9, "test-windows10/opt": 0.8},
            {"test-group1": 0.9},
            {"test-linux1804-64/opt": 0.9},
            {"test-group1": ["test-linux1804-64/opt", "test-windows10/debug"]},
        ),
    ],
)
def test_simple_schedule(
    labels_to_choose: Dict[str, float],
    groups_to_choose: Dict[str, float],
    reduced_labels: Dict[str, float],
    config_groups: Dict[str, List[str]],
    mock_hgmo: None,
    mock_repo: Tuple[str, str],
    mock_component_taskcluster_artifact: None,
    mock_schedule_tests_classify: Callable[[Dict[str, float], Dict[str, float]], None],
) -> None:
    # The repo should be almost empty at first
    repo_dir, remote_repo_dir = mock_repo
    with hglib.open(str(repo_dir)) as hg:
        logs = hg.log()
        assert len(logs) == 4
        assert [log.desc.decode("utf-8") for log in logs] == [
            "Base history 3",
            "Base history 2",
            "Base history 1",
            "Base history 0",
        ]
    with hglib.open(str(remote_repo_dir)) as hg:
        rev = hg.log()[0].node.decode("ascii")[:12]

    mock_schedule_tests_classify(labels_to_choose, groups_to_choose)

    # Scheduling a test on a revision should apply changes in the repo
    assert models.schedule_tests("mozilla-central", rev) == "OK"

    # Check changes have been applied
    with hglib.open(str(repo_dir)) as hg:
        assert len(hg.log()) == 5
        assert [log.desc.decode("utf-8") for log in hg.log()] == [
            "Pulled from remote",
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
        "reduced_tasks_higher": reduced_labels,
        "known_tasks": ["prova"],
        "config_groups": config_groups,
    }
