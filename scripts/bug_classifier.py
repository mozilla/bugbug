# -*- coding: utf-8 -*-

import argparse
import os

import numpy as np

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

    assert os.path.exists(
        model_file_name
    ), f"{model_file_name} does not exist. Train the model with trainer.py first."

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
                importance["importances"],feature_names, class_probabilities=probas
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
