# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import os
import sys
from logging import INFO, basicConfig, getLogger

import requests

from bugbug import bugzilla, similarity
from bugbug.utils import download_check_etag, zstd_decompress

basicConfig(level=INFO)
logger = getLogger(__name__)

URL = "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug.train_similarity.latest/artifacts/public/{}.zst"


def parse_args(args):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "algorithm",
        help="Similarity algorithm to use",
        choices=similarity.model_name_to_class.keys(),
    )
    parser.add_argument("bug_id", help="Bug to test", type=int)
    return parser.parse_args(args)


def main(args):

    if args.algorithm == "elasticsearch":
        model = similarity.model_name_to_class[args.algorithm]()
    else:
        model_file_name = f"{similarity.model_name_to_class[args.algorithm].__name__.lower()}.similaritymodel"

        if not os.path.exists(model_file_name):
            logger.info(f"{model_file_name} does not exist. Downloading the model....")
            try:
                download_check_etag(URL.format(model_file_name))
            except requests.HTTPError:
                logger.error(
                    "A pre-trained model is not available, you will need to train it yourself using the trainer script"
                )
                raise SystemExit(1)

            zstd_decompress(model_file_name)
            assert os.path.exists(model_file_name), "Decompressed file doesn't exist"

        model = similarity.model_name_to_class[args.algorithm].load(
            f"{similarity.model_name_to_class[args.algorithm].__name__.lower()}.similaritymodel"
        )

    bug_ids = model.get_similar_bugs(bugzilla.get(args.bug_id)[args.bug_id])

    bugs = {}
    for bug in bugzilla.get_bugs():
        if bug["id"] in bug_ids or bug["id"] == args.bug_id:
            bugs[bug["id"]] = bug

    print("{}: {}".format(args.bug_id, bugs[args.bug_id]["summary"]))
    for bug_id in bug_ids:
        print("{}: {}".format(bug_id, bugs[bug_id]["summary"]))


if __name__ == "__main__":
    main(parse_args(sys.argv[1:]))
