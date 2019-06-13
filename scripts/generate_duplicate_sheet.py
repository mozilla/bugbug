# -*- coding: utf-8 -*-

import csv
import itertools
import json
from datetime import datetime, timedelta

from bugbug import bugzilla
from bugbug.models.duplicate import DuplicateModel

m = DuplicateModel.load("duplicatemodel")

REPORTERS_TO_IGNORE = {"intermittent-bug-filer@mozilla.bugs", "wptsync@mozilla.bugs"}


try:
    with open("duplicate_test_bugs.json", "r") as f:
        test_bugs = json.load(f)
except FileNotFoundError:
    test_bug_ids = bugzilla.get_ids_between(
        datetime.now() - timedelta(days=21), datetime.now()
    )
    test_bugs = bugzilla.get(test_bug_ids)
    test_bugs = [
        bug for bug in test_bugs.values() if not bug["creator"] in REPORTERS_TO_IGNORE
    ]
    with open("duplicate_test_bugs.json", "w") as f:
        json.dump(test_bugs, f)

bug_tuples = list(itertools.combinations(test_bugs, 2))

# Warning: Classifying all the test bugs takes a while
probs = m.classify(bug_tuples, probabilities=True)

with open("duplicate_predictions.csv", "w") as csvfile:
    spamwriter = csv.writer(csvfile)

    spamwriter.writerow(
        ["bug 1 ID", "bug 1 summary", "bug 2 ID", "bug 2 summary", "prediction"]
    )

    for bug_tuple, prob in zip(bug_tuples, probs):

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
