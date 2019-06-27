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
        "docker_image",
        metavar="docker-image",
        type=str,
        help="The name of the docker image to set version for",
    )
    parser.add_argument(
        "version", metavar="version", type=str, help="The docker image version to set"
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

    if task_image and task_image.split(":", 1)[0] == args.docker_image:
        task_payload["image"] = f"{args.docker_image}:{args.version}"

    with open(args.hook_file, "w") as hook_file:
        json.dump(
            hook_data, hook_file, sort_keys=True, indent=4, separators=(",", ": ")
        )


if __name__ == "__main__":
    main(sys.argv[1:])
