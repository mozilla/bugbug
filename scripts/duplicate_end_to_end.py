# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

# This file runs the duplicate model end-to-end,
# Prerequisites:
# 1. Train a similarity model to get bugs similar to a given bug
# 2. Train the duplicate model independently
# During testing, we first generate all bugs similar to a bug using model 1,
# then apply the trained duplicate model to this set of similar bugs and classify
# the bug as duplicate if the probability score is greater than a threshold. (0.8)

# The training steps are skipped here; this can already be done through similarity_trainer.py
# and trainer.py. Hence, **similaritymodel** and **duplicatemodel** pretrained files
# are already available.

import argparse
import csv
import os
import sys
from datetime import datetime, timedelta
from logging import INFO, basicConfig, getLogger

import requests

from bugbug import bugzilla, similarity
from bugbug.models.duplicate import DuplicateModel
from bugbug.utils import download_check_etag, zstd_decompress

basicConfig(level=INFO)
logger = getLogger(__name__)

URL = "https://community-tc.services.mozilla.com/api/index/v1/task/project.relman.bugbug.train_similarity.latest/artifacts/public/{}.zst"

REPORTERS_TO_IGNORE = {"intermittent-bug-filer@mozilla.bugs", "wptsync@mozilla.bugs"}


def parse_args(args):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "similaritymodel",
        help="Similarity algorithm to test",
        choices=similarity.model_name_to_class.keys(),
    )

    # At the moment, we use LinearSVC
    parser.add_argument("duplicatemodel", help="Duplicate algorithm to test")
    return parser.parse_args(args)


def load_models(args):
    if args.similaritymodel == "elasticsearch":
        similarity_model = similarity.model_name_to_class[args.similaritymodel]()
    else:
        model_file_name = f"{similarity.model_name_to_class[args.similaritymodel].__name__.lower()}.similaritymodel"

        if not os.path.exists(model_file_name):
            logger.info(f"{model_file_name} does not exist. Downloading the model....")
            try:
                download_check_etag(URL.format(model_file_name))
            except requests.HTTPError:
                logger.error(
                    f"A pre-trained model is not available, you will need to train it yourself using the trainer script"
                )
                raise SystemExit(1)

            zstd_decompress(model_file_name)
            assert os.path.exists(model_file_name), "Decompressed file doesn't exist"

        similarity_model = similarity.model_name_to_class[args.similaritymodel].load(
            f"{similarity.model_name_to_class[args.similaritymodel].__name__.lower()}.similaritymodel"
        )

    if args.duplicatemodel == "linearsvc":
        duplicate_model = DuplicateModel.load("duplicatemodel")
    else:
        logger.error(f"Define the duplicate model to use")
        raise SystemExit(1)

    return similarity_model, duplicate_model


def test(similaritymodel, duplicatemodel):
    test_bug_ids = bugzilla.get_ids_between(
        datetime.now() - timedelta(days=7), datetime.now()
    )

    print(f"Testing against {len(test_bug_ids)} bugs")
    test_bugs = bugzilla.get(test_bug_ids)
    test_bugs = [
        bug for bug in test_bugs.values() if not bug["creator"] in REPORTERS_TO_IGNORE
    ]
    with open("end_to_end_duplicate_predictions.csv", "w") as csvfile:
        spamwriter = csv.writer(csvfile)

        spamwriter.writerow(
            ["bug 1 ID", "bug 1 summary", "bug 2 ID", "bug 2 summary", "prediction"]
        )
        for test_bug in test_bugs:
            similar_bugs_id = similaritymodel.get_similar_bugs(test_bug)
            sim_bugs = bugzilla.get(similar_bugs_id)
            test_tuple = [
                (sim_bugs[similar_bug_id], test_bug) for similar_bug_id in sim_bugs
            ]
            probs = duplicatemodel.classify(test_tuple, probabilities=True)

            for bug_tuple, prob in zip(test_tuple, probs):
                if prob[1] > 0.8:
                    spamwriter.writerow(
                        [
                            f'https://bugzilla.mozilla.org/show_bug.cgi?id={bug_tuple[0]["id"]}',
                            bug_tuple[0]["summary"],
                            f'https://bugzilla.mozilla.org/show_bug.cgi?id={bug_tuple[1]["id"]}',
                            bug_tuple[1]["summary"],
                            prob[1],
                        ]
                    )


def main(args):
    models = load_models(args)
    test(models[0], models[1])


if __name__ == "__main__":
    main(parse_args(sys.argv[1:]))
