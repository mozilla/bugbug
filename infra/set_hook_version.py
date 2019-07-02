# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import json
import sys


def main(raw_args):
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

    args = parser.parse_args(raw_args)

    with open(args.hook_file, "r") as hook_file:
        hook_data = json.load(hook_file)

    task_payload = hook_data["task"]["payload"]

    task_image = task_payload.get("image")

    # 1) Insert or replace the environment variable
    hook_env = task_payload["env"]
    hook_env["TAG"] = args.version

    # 2) Set the version for the hook docker image
    if task_image and task_image.split(":", 1)[0] == "mozilla/bugbug-spawn-pipeline":
        task_payload["image"] = f"mozilla/bugbug-spawn-pipeline:{args.version}"

    with open(args.hook_file, "w") as hook_file:
        json.dump(
            hook_data, hook_file, sort_keys=True, indent=4, separators=(",", ": ")
        )


if __name__ == "__main__":
    main(sys.argv[1:])
