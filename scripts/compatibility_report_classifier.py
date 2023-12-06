# -*- coding: utf-8 -*-

import argparse
import os
from logging import INFO, basicConfig, getLogger

import numpy as np
import requests

from bugbug.models import get_model_class
from bugbug.utils import download_model

basicConfig(level=INFO)
logger = getLogger(__name__)


def classify_reports(model_name: str, report_text: str) -> None:
    model_file_name = f"{model_name}model"

    if not os.path.exists(model_file_name):
        logger.info("%s does not exist. Downloading the model....", model_file_name)
        try:
            download_model(model_name)
        except requests.HTTPError:
            logger.error(
                "A pre-trained model is not available, you will need to train it yourself using the trainer script"
            )
            raise SystemExit(1)

    model_class = get_model_class(model_name)
    model = model_class.load(model_file_name)

    logger.info("%s", report_text)

    report = {"body": report_text, "title": ""}

    if model.calculate_importance:
        probas, importance = model.classify(
            report, probabilities=True, importances=True
        )

        model.print_feature_importances(
            importance["importances"], class_probabilities=probas
        )
    else:
        probas = model.classify(report, probabilities=True, importances=False)

    probability = probas[0]
    pred_index = np.argmax(probability)
    if len(probability) > 2:
        pred_class = model.le.inverse_transform([pred_index])[0]
    else:
        pred_class = "Positive" if pred_index == 1 else "Negative"
    logger.info("%s %s", pred_class, probability)
    input()


def main() -> None:
    description = "Perform evaluation of user report using the specified model"
    parser = argparse.ArgumentParser(description=description)

    parser.add_argument("model", type=str, help="Which model to use for evaluation")
    parser.add_argument("--report-text", help="Report text to classify", type=str)

    args = parser.parse_args()

    classify_reports(args.model, args.report_text)


if __name__ == "__main__":
    main()
