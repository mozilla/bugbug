# -*- coding: utf-8 -*-

import argparse
import json
import os
import sys
from logging import INFO, basicConfig, getLogger

from bugbug import bugzilla, db, model, repository
from bugbug.models import get_model_class
from bugbug.utils import CustomJsonEncoder, zstd_compress

MODELS_WITH_TYPE = ("component",)
HISTORICAL_SUPPORTED_TASKS = (
    "defect",
    "bugtype",
    "defectenhancementtask",
    "regression",
)

basicConfig(level=INFO)
logger = getLogger(__name__)


class Trainer(object):
    def go(self, args):
        # Download datasets that were built by bugbug_data.
        os.makedirs("data", exist_ok=True)

        if args.classifier != "default":
            assert (
                args.model in MODELS_WITH_TYPE
            ), f"{args.classifier} is not a valid classifier type for {args.model}"

            model_name = f"{args.model}_{args.classifier}"
        else:
            model_name = args.model

        model_class = get_model_class(model_name)
        if args.model in HISTORICAL_SUPPORTED_TASKS:
            model_obj = model_class(args.lemmatization, args.historical)
        elif args.model == "duplicate":
            model_obj = model_class(
                args.training_set_size, args.lemmatization, args.cleanup_urls
            )
        else:
            model_obj = model_class(args.lemmatization)

        if (
            isinstance(model_obj, model.BugModel)
            or isinstance(model_obj, model.BugCoupleModel)
            or (hasattr(model_obj, "bug_data") and model_obj.bug_data)
        ):
            db.download(bugzilla.BUGS_DB)

        if isinstance(model_obj, model.CommitModel):
            db.download(repository.COMMITS_DB)

        logger.info(f"Training *{model_name}* model")
        metrics = model_obj.train()

        # Save the metrics as a file that can be uploaded as an artifact.
        metric_file_path = "metrics.json"
        with open(metric_file_path, "w") as metric_file:
            json.dump(metrics, metric_file, cls=CustomJsonEncoder)

        logger.info(f"Training done")

        model_file_name = f"{model_name}model"
        assert os.path.exists(model_file_name)
        zstd_compress(model_file_name)

        logger.info(f"Model compressed")


def parse_args(args):
    description = "Train the models"
    parser = argparse.ArgumentParser(description=description)

    parser.add_argument("model", help="Which model to train.")
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


def main():
    args = parse_args(sys.argv[1:])

    retriever = Trainer()
    retriever.go(args)
