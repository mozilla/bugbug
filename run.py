# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import sys

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
    parser.add_argument(
        "--disable-url-cleanup",
        help="Don't cleanup urls when training the duplicate model",
        dest="cleanup_urls",
        default=True,
        action="store_false",
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
    parser.add_argument(
        "--historical",
        help="""Analyze historical bugs. Only used for defect, bugtype,
                defectenhancementtask and regression tasks.""",
        action="store_true",
    )
    return parser.parse_args(args)


def main(args):
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
            model = model_class(
                args.training_set_size, args.lemmatization, args.cleanup_urls
            )
        else:
            model = model_class(args.lemmatization)
        model.train()


if __name__ == "__main__":
    main(parse_args(sys.argv[1:]))
