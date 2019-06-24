# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import csv
import os
import sys
from datetime import datetime, timedelta

import numpy as np

from bugbug import bugzilla, db, repository
from bugbug.models import MODELS, get_model_class


def parse_args(args):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--lemmatization",
        help="Perform lemmatization (using spaCy)",
        action="store_true",
    )
    parser.add_argument(
        "--training-set-size",
        nargs="?",
        default=14000,
        type=int,
        help="The size of the training set for the duplicate model",
    )
    parser.add_argument("--train", help="Perform training", action="store_true")
    parser.add_argument(
        "--goal", help="Goal of the classifier", choices=MODELS.keys(), default="defect"
    )
    parser.add_argument(
        "--classifier",
        help="Type of the classifier. Only used for component classification.",
        choices=["default", "nn"],
        default="default",
    )
    parser.add_argument("--classify", help="Perform evaluation", action="store_true")
    parser.add_argument(
        "--generate-sheet",
        help="Perform evaluation on bugs from last week and generate a csv file",
        action="store_true",
    )
    parser.add_argument("--token", help="Bugzilla token", action="store")
    parser.add_argument(
        "--historical",
        help="""Analyze historical bugs. Only used for defect, bugtype,
                defectenhancementtask and regression tasks.""",
        action="store_true",
    )
    return parser.parse_args(args)


def main(args):
    model_file_name = "{}{}model".format(
        args.goal, "" if args.classifier == "default" else args.classifier
    )

    if args.goal == "component":
        if args.classifier == "default":
            model_class_name = "component"
        else:
            model_class_name = "component_nn"
    else:
        model_class_name = args.goal

    model_class = get_model_class(model_class_name)

    if args.train:
        db.download(bugzilla.BUGS_DB)
        db.download(repository.COMMITS_DB)

        historical_supported_tasks = [
            "defect",
            "bugtype",
            "defectenhancementtask",
            "regression",
        ]

        if args.goal in historical_supported_tasks:
            model = model_class(args.lemmatization, args.historical)
        elif args.goal == "duplicate":
            model = model_class(args.training_set_size, args.lemmatization)
        else:
            model = model_class(args.lemmatization)
        model.train()
    else:
        model = model_class.load(model_file_name)

    if args.classify:
        for bug in bugzilla.get_bugs():
            print(
                f'https://bugzilla.mozilla.org/show_bug.cgi?id={ bug["id"] } - { bug["summary"]} '
            )

            if model.calculate_importance:
                probas, importances = model.classify(
                    bug, probabilities=True, importances=True
                )

                feature_names = model.get_feature_names()
                for i, (importance, index, is_positive) in enumerate(importances):
                    print(
                        f'{i + 1}. \'{feature_names[int(index)]}\' ({"+" if (is_positive) else "-"}{importance})'
                    )
            else:
                probas = model.classify(bug, probabilities=True, importances=False)

            if np.argmax(probas) == 1:
                print(f"Positive! {probas}")
            else:
                print(f"Negative! {probas}")
            input()

    if args.generate_sheet:
        assert (
            args.token is not None
        ), "A Bugzilla token should be set in order to download bugs"
        today = datetime.utcnow()
        a_week_ago = today - timedelta(7)
        bugzilla.set_token(args.token)
        bugs = bugzilla.download_bugs_between(a_week_ago, today)

        print(f"Classifying {len(bugs)} bugs...")

        rows = [["Bug", f"{args.goal}(model)", args.goal, "Title"]]

        for bug in bugs:
            p = model.classify(bug, probabilities=True)
            rows.append(
                [
                    f'https://bugzilla.mozilla.org/show_bug.cgi?id={bug["id"]}',
                    "y" if p[0][1] >= 0.7 else "n",
                    "",
                    bug["summary"],
                ]
            )

        os.makedirs("sheets", exist_ok=True)
        with open(
            os.path.join(
                "sheets",
                f'{args.goal}-{datetime.utcnow().strftime("%Y-%m-%d")}-labels.csv',
            ),
            "w",
        ) as f:
            writer = csv.writer(f)
            writer.writerows(rows)


if __name__ == "__main__":
    main(parse_args(sys.argv[1:]))
