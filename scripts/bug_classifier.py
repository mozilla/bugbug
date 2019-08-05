# -*- coding: utf-8 -*-

import argparse
import os

import numpy as np
import requests
import zstandard

from bugbug import bugzilla
from bugbug.models import get_model_class

MODELS_WITH_TYPE = ("component",)


def classify_bugs(model_name, classifier):
    if classifier != "default":
        assert (
            model_name in MODELS_WITH_TYPE
        ), f"{classifier} is not a valid classifier type for {model_name}"

        model_file_name = f"{model_name}{classifier}model"
        model_name = f"{model_name}_{classifier}"
    else:
        model_file_name = f"{model_name}model"

    if not os.path.exists(model_file_name):
        print(f"{model_file_name} does not exist. Downloading the model....")
        download_url = f"https://index.taskcluster.net/v1/task/project.relman.bugbug.train_{model_name}.latest/artifacts/public/{model_file_name}.zst"
        r = requests.get(download_url, stream=True)
        assert (
            r
        ), f"{model_file_name} isn't available to download. Train the model with trainer.py first."

        with open(f"{model_file_name}.zst", "wb") as f:
            for chunk in r.iter_content(chunk_size=4096):
                f.write(chunk)

        dctx = zstandard.ZstdDecompressor()
        with open(f"{model_file_name}.zst", "rb") as input_f:
            with open(f"{model_file_name}", "wb") as output_f:
                dctx.copy_stream(input_f, output_f)
        assert os.path.exists(f"{model_file_name}"), "Decompressed file doesn't exist"

    model_class = get_model_class(model_name)
    model = model_class.load(model_file_name)

    for bug in bugzilla.get_bugs():
        print(
            f'https://bugzilla.mozilla.org/show_bug.cgi?id={bug["id"]} - {bug["summary"]} '
        )

        if model.calculate_importance:
            probas, importance = model.classify(
                bug, probabilities=True, importances=True
            )

            feature_names = model.get_human_readable_feature_names()

            model.print_feature_importances(
                importance["importances"], feature_names, class_probabilities=probas
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

    args = parser.parse_args()

    classify_bugs(args.model, args.classifier)
