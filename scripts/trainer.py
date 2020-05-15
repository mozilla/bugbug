# -*- coding: utf-8 -*-

import argparse
import json
import os
import sys
from logging import INFO, basicConfig, getLogger

from bugbug import db
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
        elif args.model == "regressor":
            model_obj = model_class(args.lemmatization, args.interpretable)
        elif args.model == "duplicate":
            model_obj = model_class(
                args.training_set_size, args.lemmatization, args.cleanup_urls
            )
        else:
            model_obj = model_class(args.lemmatization)

        if args.download_db:
            for required_db in model_obj.training_dbs:
                assert db.download(required_db)

            if args.download_eval:
                model_obj.download_eval_dbs()
        else:
            logger.info("Skipping download of the databases")

        logger.info(f"Training *{model_name}* model")
        metrics = model_obj.train(limit=args.limit)

        # Save the metrics as a file that can be uploaded as an artifact.
        metric_file_path = "metrics.json"
        with open(metric_file_path, "w") as metric_file:
            json.dump(metrics, metric_file, cls=CustomJsonEncoder)

        logger.info("Training done")

        model_file_name = f"{model_name}model"
        assert os.path.exists(model_file_name)
        zstd_compress(model_file_name)

        logger.info("Model compressed")

        if model_obj.store_dataset:
            assert os.path.exists(f"{model_file_name}_data_X")
            zstd_compress(f"{model_file_name}_data_X")
            assert os.path.exists(f"{model_file_name}_data_y")
            zstd_compress(f"{model_file_name}_data_y")


def parse_args(args):
    description = "Train the models"
    parser = argparse.ArgumentParser(description=description)

    parser.add_argument("model", help="Which model to train.")
    parser.add_argument(
        "--limit",
        type=int,
        help="Only train on a subset of the data, used mainly for integrations tests",
    )
    parser.add_argument(
        "--no-download",
        action="store_false",
        dest="download_db",
        help="Do not download databases, uses whatever is on disk",
    )
    parser.add_argument(
        "--download-eval",
        action="store_true",
        dest="download_eval",
        help="Download databases and database support files required at runtime (e.g. if the model performs custom evaluations)",
    )
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
    parser.add_argument(
        "--interpretable",
        help="""Only use human-interpretable features. Only used for regressor task.""",
        action="store_true",
    )
    return parser.parse_args(args)


def main():
    args = parse_args(sys.argv[1:])

    retriever = Trainer()
    retriever.go(args)


if __name__ == "__main__":
    main()
