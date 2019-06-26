# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import sys

from bugbug.similarity import LSISimilarity, NeighborsSimilarity


def parse_args(args):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--algorithm", help="Similarity algorithm to use", choices=["lsi", "neighbors"]
    )
    return parser.parse_args(args)


def main(args):
    if args.algorithm == "lsi":
        model = LSISimilarity()
    elif args.algorithm == "neighbors":
        model = NeighborsSimilarity()

    model.evaluation()


if __name__ == "__main__":
    main(parse_args(sys.argv[1:]))
