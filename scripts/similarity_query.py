# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import sys

from bugbug import bugzilla, similarity


def parse_args(args):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--algorithm",
        help="Similarity algorithm to use",
        choices=similarity.model_name_to_class.keys(),
    )
    parser.add_argument("--bug_id", help="Bug to test")
    return parser.parse_args(args)


def main(args):
    model = similarity.model_name_to_class[args.algorithm].load(
        f"{similarity.model_name_to_class[args.algorithm].__name__.lower()}.similaritymodel"
    )

    print(model.get_similar_bugs(bugzilla.get(int(args.bug_id))[int(args.bug_id)]))


if __name__ == "__main__":
    main(parse_args(sys.argv[1:]))
