# -*- coding: utf-8 -*-

import argparse
import os
from logging import INFO, basicConfig, getLogger

import numpy as np
import requests

from bugbug import bugzilla, db
from bugbug.models import get_model_class
from bugbug.utils import download_model

MODELS_WITH_TYPE = ("component",)

basicConfig(level=INFO)
logger = getLogger(__name__)


def classify_bugs(model_name: str, classifier: str, bug_id: int) -> None:
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
            download_model(model_name)
        except requests.HTTPError:
            logger.error(
                "A pre-trained model is not available, you will need to train it yourself using the trainer script"
            )
            raise SystemExit(1)

    model_class = get_model_class(model_name)
    model = model_class.load(model_file_name)

    if bug_id:
        bugs = bugzilla.get(bug_id).values()
        assert bugs, f"A bug with a bug id of {bug_id} was not found"
    else:
        assert db.download(bugzilla.BUGS_DB)
        bugs = bugzilla.get_bugs()

    for bug in bugs:
        print(
            f'https://bugzilla.mozilla.org/show_bug.cgi?id={bug["id"]} - {bug["summary"]} '
        )

        if model.calculate_importance:
            probas, importance = model.classify(
                bug, probabilities=True, importances=True
            )

            model.print_feature_importances(
                importance["importances"], class_probabilities=probas
            )
        else:
            probas = model.classify(bug, probabilities=True, importances=False)

        probability = probas[0]
        pred_index = np.argmax(probability)
        if len(probability) > 2:
            pred_class = model.le.inverse_transform([pred_index])[0]
        else:
            pred_class = "Positive" if pred_index == 1 else "Negative"
        print(f"{pred_class} {probability}")
        input()


def main() -> None:
    description = "Perform evaluation on bugs using the specified model"
    parser = argparse.ArgumentParser(description=description)

    parser.add_argument("model", help="Which model to use for evaluation")
    parser.add_argument(
        "--classifier",
        help="Type of the classifier. Only used for component classification.",
        choices=["default", "nn"],
        default="default",
    )
    parser.add_argument("--bug-id", help="Classify the given bug id", type=int)

    args = parser.parse_args()

    classify_bugs(args.model, args.classifier, args.bug_id)


if __name__ == "__main__":
    main()
