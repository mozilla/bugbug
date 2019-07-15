#!/bin/env python
# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import logging
import os
import sys
import urllib
from urllib.request import urlretrieve

import requests.packages.urllib3
import taskcluster

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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


def download_artifacts(queue, task_ids):
    artifact = "public/done"

    for task_id in task_ids:
        logger.info(f"Download from {task_id}")

        # Build artifact url
        try:
            url = queue.buildSignedUrl("getLatestArtifact", task_id, artifact)
        except taskcluster.exceptions.TaskclusterAuthFailure:
            url = queue.buildUrl("getLatestArtifact", task_id, artifact)

        logger.info(f"Downloading {url}")

        try:
            urlretrieve(url, task_id)
            yield task_id
        except urllib.error.HTTPError as e:
            if e.getcode() == 404:
                pass


def parse_args(args):
    parser = argparse.ArgumentParser()
    parser.add_argument("group_id", type=str, help="Hook group ID")
    parser.add_argument("id", type=str, help="Hook ID")
    return parser.parse_args(args)


def main(args):
    options = get_taskcluster_options()

    queue = taskcluster.Queue(options)

    task = queue.task(os.environ["TASK_ID"])
    assert len(task["dependencies"]) > 0, "No task dependencies"

    artifacts = download_artifacts(queue, task["dependencies"])

    should_trigger = False
    for artifact in artifacts:
        with open(artifact, "r") as f:
            done = int(f.read()) == 1

        if not done:
            should_trigger = True
            break

    if should_trigger:
        hooks = taskcluster.Hooks(options)
        hooks.ping()
        hooks.triggerHook(args.group_id, args.id, {})


if __name__ == "__main__":
    main(parse_args(sys.argv[1:]))
