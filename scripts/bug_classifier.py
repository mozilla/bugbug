# -*- coding: utf-8 -*-

import argparse
import os
from logging import INFO, basicConfig, getLogger

import numpy as np
import requests

from bugbug import bugzilla
from bugbug.models import get_model_class
from bugbug.utils import download_check_etag, zstd_decompress

MODELS_WITH_TYPE = ("component",)

basicConfig(level=INFO)
logger = getLogger(__name__)


def classify_bugs(model_name, classifier, bug_id):
    if classifier != "default":
        assert (
            model_name in MODELS_WITH_TYPE
        ), f"{classifier} is not a valid classifier type for {model_name}"

        model_file_name = f"{model_name}{classifier}model"
        model_name = f"{model_name}_{classifier}"
    else:
        model_file_name = f"{model_name}model"

    if not os.path.exists(model_file_name):
        logger.info(f"{model_file_name} does not exist. Downloading the model....")
        try:
            download_check_etag(
                f"https://index.taskcluster.net/v1/task/project.relman.bugbug.train_{model_name}.latest/artifacts/public/{model_file_name}.zst",
                f"{model_file_name}.zst",
            )
        except requests.HTTPError:
            logger.error(
                f"A pre-trained model is not available, you will need to train it yourself using the trainer script"
            )
            raise SystemExit(1)

        zstd_decompress(model_file_name)
        assert os.path.exists(model_file_name), "Decompressed file doesn't exist"

    model_class = get_model_class(model_name)
    model = model_class.load(model_file_name)

    if bug_id:
        bugs = bugzilla.get(bug_id).values()
        assert bugs, f"A bug with a bug id of {bug_id} was not found"
    else:
        bugs = bugzilla.get_bugs()

    for bug in bugs:
        print(
            f'https://bugzilla.mozilla.org/show_bug.cgi?id={bug["id"]} - {bug["summary"]} '
        )

        if model.calculate_importance:
            probas, importance = model.classify(
                bug, probabilities=True, importances=True
            )

            feature_names = model.get_human_readable_feature_names()

            model.print_feature_importances(
                importance["importances"], class_probabilities=probas
            )
        else:
            probas = model.classify(bug, probabilities=True, importances=False)

        if np.argmax(probas) == 1:
            print(f"Positive! {probas}")
        else:
            print(f"Negative! {probas}")
        input()


def main():
    description = "Perform evaluation on bugs using the specified model"
    parser = argparse.ArgumentParser(description=description)

    parser.add_argument("model", help="Which model to use for evaluation")
    parser.add_argument(
        "--classifier",
        help="Type of the classifier. Only used for component classification.",
        choices=["default", "nn"],
        default="default",
    )
    parser.add_argument("--bug-id", help="Classify the given bug id")

    args = parser.parse_args()

    classify_bugs(args.model, args.classifier, args.bug_id)
