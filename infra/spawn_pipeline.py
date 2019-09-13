#!/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2019 Mozilla
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
This script triggers the data pipeline for the bugbug project
"""

import argparse
import os
import sys

import jsone
import requests.packages.urllib3
import taskcluster
import yaml

requests.packages.urllib3.disable_warnings()

TASKCLUSTER_DEFAULT_URL = "https://taskcluster.net"


def get_taskcluster_options():
    """
    Helper to get the Taskcluster setup options
    according to current environment (local or Taskcluster)
    """
    options = taskcluster.optionsFromEnvironment()
    proxy_url = os.environ.get("TASKCLUSTER_PROXY_URL")

    if proxy_url is not None:
        # Always use proxy url when available
        options["rootUrl"] = proxy_url

    if "rootUrl" not in options:
        # Always have a value in root url
        options["rootUrl"] = TASKCLUSTER_DEFAULT_URL

    return options


def main():
    parser = argparse.ArgumentParser(description="Spawn tasks for bugbug data pipeline")
    parser.add_argument("data_pipeline_json")

    args = parser.parse_args()
    decision_task_id = os.environ.get("TASK_ID")
    options = get_taskcluster_options()
    add_self = False
    if decision_task_id:
        add_self = True
        task_group_id = decision_task_id
    else:
        task_group_id = taskcluster.utils.slugId()
    keys = {"taskGroupId": task_group_id}

    id_mapping = {}

    # First pass, do the template rendering and dependencies resolution
    tasks = []

    with open(args.data_pipeline_json) as pipeline_file:
        raw_tasks = yaml.safe_load(pipeline_file.read())

    context = {"version": os.getenv("TAG", "latest")}
    rendered = jsone.render(raw_tasks, context)

    for task in rendered["tasks"]:
        # We need to generate new unique task ids for taskcluster to be happy
        # but need to identify dependencies across tasks. So we create a
        # mapping between an internal ID and the generate ID

        task_id = taskcluster.utils.slugId()
        task_internal_id = task.pop("ID")

        if task_internal_id in id_mapping:
            raise ValueError(f"Conflicting IDs {task_internal_id}")

        id_mapping[task_internal_id] = task_id

        for key, value in keys.items():
            task[key] = value

        # Process the dependencies
        new_dependencies = []
        for dependency in task.get("dependencies", []):
            new_dependencies.append(id_mapping[dependency])

        if add_self:
            new_dependencies.append(decision_task_id)

        task["dependencies"] = new_dependencies

        tasks.append((task_id, task))

    # Now sends them
    queue = taskcluster.Queue(options)
    try:
        for task_id, task_payload in tasks:
            queue.createTask(task_id, task_payload)

        print(f"https://tools.taskcluster.net/task-group-inspector/#/{task_group_id}")
    except taskcluster.exceptions.TaskclusterAuthFailure as e:
        print(f"TaskclusterAuthFailure: {e.body}", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
