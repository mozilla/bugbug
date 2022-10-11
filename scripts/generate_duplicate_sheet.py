# -*- coding: utf-8 -*-

import argparse
import csv
import json
import sys
from datetime import datetime, timedelta
from itertools import combinations
from logging import INFO, basicConfig, getLogger

from bugbug import bugzilla, similarity
from bugbug.models.duplicate import DuplicateModel

basicConfig(level=INFO)
logger = getLogger(__name__)

REPORTERS_TO_IGNORE = {"intermittent-bug-filer@mozilla.bugs", "wptsync@mozilla.bugs"}


def parse_args(args):
    parser = argparse.ArgumentParser()
    parser.add_argument("--similaritymodel", default=None)
    return parser.parse_args(args)


def main(args):
    similarity_model = (
        similarity.download_and_load_similarity_model(args.similaritymodel)
        if args.similaritymodel
        else None
    )
    duplicate_model = DuplicateModel.load("duplicatemodel")
    try:
        with open("duplicate_test_bugs.json", "r") as f:
            test_bugs = json.load(f)
    except FileNotFoundError:
        test_bug_ids = bugzilla.get_ids_between(datetime.now() - timedelta(days=21))
        test_bugs = bugzilla.get(test_bug_ids)
        test_bugs = [
            bug
            for bug in test_bugs.values()
            if not bug["creator"] in REPORTERS_TO_IGNORE
        ]
        with open("duplicate_test_bugs.json", "w") as f:
            json.dump(test_bugs, f)

    with open("duplicate_predictions.csv", "w") as csvfile:
        spamwriter = csv.writer(csvfile)

        spamwriter.writerow(
            ["bug 1 ID", "bug 1 summary", "bug 2 ID", "bug 2 summary", "prediction"]
        )
        if similarity_model:
            bug_tuples = []
            for test_bug in test_bugs:
                similar_bug_ids = similarity_model.get_similar_bugs(test_bug)
                similar_bugs = bugzilla.get(similar_bug_ids)
                bug_tuples += [
                    (test_bug, similar_bug) for similar_bug in similar_bugs.values()
                ]
        else:
            bug_tuples = combinations(test_bugs, 2)

        probs = duplicate_model.classify(bug_tuples, probabilities=True)

        for bug_tuple, prob in zip(bug_tuples, probs):
            if prob[1] > similarity_model.confidence_threshold:
                spamwriter.writerow(
                    [
                        f'https://bugzilla.mozilla.org/show_bug.cgi?id={bug_tuple[0]["id"]}',
                        bug_tuple[0]["summary"],
                        f'https://bugzilla.mozilla.org/show_bug.cgi?id={bug_tuple[1]["id"]}',
                        bug_tuple[1]["summary"],
                        prob[1],
                    ]
                )


if __name__ == "__main__":
    main(parse_args(sys.argv[1:]))
