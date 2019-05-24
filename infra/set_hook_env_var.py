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
        "env_name",
        metavar="env-name",
        type=str,
        help="The name of the environment variable to set",
    )
    parser.add_argument(
        "env_value",
        metavar="env-value",
        type=str,
        help="The value of the environment variable to set",
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

    # Insert or replace the environment variable
    hook_env = hook_data["task"]["payload"]["env"]
    hook_env[args.env_name] = args.env_value

    with open(args.hook_file, "w") as hook_file:
        json.dump(
            hook_data, hook_file, sort_keys=True, indent=4, separators=(",", ": ")
        )


if __name__ == "__main__":
    main(sys.argv[1:])
