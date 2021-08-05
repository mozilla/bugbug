# -*- coding: utf-8 -*-

import argparse
import os
from logging import INFO, basicConfig, getLogger

import numpy as np
import requests

from bugbug import db
from bugbug.github import Github
from bugbug.models import get_model_class
from bugbug.utils import download_model

basicConfig(level=INFO)
logger = getLogger(__name__)


def classify_issues(
    owner: str, repo: str, retrieve_events: bool, model_name: str, issue_number: int
) -> None:

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

    github = Github(
        owner=owner, repo=repo, state="all", retrieve_events=retrieve_events
    )

    if issue_number:
        issues = iter(
            [github.fetch_issue_by_number(owner, repo, issue_number, retrieve_events)]
        )
        assert issues, f"An issue with a number of {issue_number} was not found"
    else:
        assert db.download(github.db_path)
        issues = github.get_issues()

    for issue in issues:
        print(f'{issue["url"]} - {issue["title"]} ')

        if model.calculate_importance:
            probas, importance = model.classify(
                issue, probabilities=True, importances=True
            )

            model.print_feature_importances(
                importance["importances"], class_probabilities=probas
            )
        else:
            probas = model.classify(issue, probabilities=True, importances=False)

        probability = probas[0]
        pred_index = np.argmax(probability)
        if len(probability) > 2:
            pred_class = model.le.inverse_transform([pred_index])[0]
        else:
            pred_class = "Positive" if pred_index == 1 else "Negative"
        print(f"{pred_class} {probability}")
        input()


def main() -> None:
    description = "Perform evaluation on github issues using the specified model"
    parser = argparse.ArgumentParser(description=description)

    parser.add_argument("model", type=str, help="Which model to use for evaluation")
    parser.add_argument(
        "--owner",
        help="GitHub repository owner.",
        type=str,
        required=True,
    )
    parser.add_argument(
        "--repo",
        help="GitHub repository name.",
        type=str,
        required=True,
    )
    parser.add_argument(
        "--retrieve-events",
        action="store_true",
        help="Whether to retrieve events for each issue.",
    )

    parser.add_argument(
        "--issue-number", help="Classify the given github issue by number", type=int
    )

    args = parser.parse_args()

    classify_issues(
        args.owner, args.repo, args.retrieve_events, args.model, args.issue_number
    )


if __name__ == "__main__":
    main()
