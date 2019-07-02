# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import json
import sys


def set_hook(hook_path, version):
    with open(hook_path, "r") as hook_file:
        hook_data = json.load(hook_file)

    task_payload = hook_data["task"]["payload"]

    task_image = task_payload.get("image")

    # 1) Insert or replace the environment variable
    if task_payload["env"]:
        task_payload["env"] = {"$merge": [{"TAG": version}, task_payload["env"]]}
    else:
        task_payload["env"]["TAG"] = version

    # 2) Set the version for the hook docker image
    if task_image and task_image.split(":", 1)[0] == "mozilla/bugbug-spawn-pipeline":
        task_payload["image"] = f"mozilla/bugbug-spawn-pipeline:{version}"

    with open(hook_path, "w") as hook_file:
        json.dump(
            hook_data, hook_file, sort_keys=True, indent=4, separators=(",", ": ")
        )


def parse_args(raw_args):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "version",
        metavar="version",
        type=str,
        help="The version to set in the hook definition",
    )
    parser.add_argument(
        "hook_file",
        metavar="hook-file",
        type=str,
        help="The hook definition file to update in-place",
    )

    return parser.parse_args(raw_args)


if __name__ == "__main__":
    args = parse_args(sys.argv[1:])
    set_hook(args.hook_file, args.version)
