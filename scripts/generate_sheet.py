# -*- coding: utf-8 -*-

import argparse
import csv
import os
from datetime import datetime, timedelta

import numpy as np

from bugbug import bugzilla
from bugbug.models import get_model_class


def generate_sheet(model_name: str, token: str, days: int, threshold: float) -> None:
    model_file_name = f"{model_name}model"

    assert os.path.exists(
        model_file_name
    ), f"{model_file_name} does not exist. Train the model with trainer.py first."

    model_class = get_model_class(model_name)
    model = model_class.load(model_file_name)

    today = datetime.utcnow()
    start_date = today - timedelta(days)
    bugzilla.set_token(token)
    bug_ids = bugzilla.get_ids_between(start_date, today)
    bugs = bugzilla.get(bug_ids)

    print(f"Classifying {len(bugs)} bugs...")

    rows = [["Bug", f"{model_name}(model)", model_name, "Title"]]

    for bug in bugs.values():
        p = model.classify(bug, probabilities=True)
        probability = p[0]
        if len(probability) > 2:
            index = np.argmax(probability)
            prediction = model.class_names[index]
        else:
            prediction = "y" if probability[1] >= threshold else "n"

        rows.append(
            [
                f'https://bugzilla.mozilla.org/show_bug.cgi?id={bug["id"]}',
                prediction,
                "",
                bug["summary"],
            ]
        )

    os.makedirs("sheets", exist_ok=True)
    with open(
        os.path.join(
            "sheets",
            f'{model_name}-{datetime.utcnow().strftime("%Y-%m-%d")}-labels.csv',
        ),
        "w",
    ) as f:
        writer = csv.writer(f)
        writer.writerows(rows)


def main() -> None:
    description = "Perform evaluation on bugs from specified days back on the specified model and generate a csv file "
    parser = argparse.ArgumentParser(description=description)

    parser.add_argument("model", help="Which model to generate a csv for.")
    parser.add_argument("token", help="Bugzilla token")
    parser.add_argument(
        "days",
        type=int,
        default=7,
        help="No. of days back from which bugs will be evaluated",
    )
    parser.add_argument(
        "threshold", type=float, default=0.7, help="Confidence threshold for the model"
    )

    args = parser.parse_args()

    generate_sheet(args.model, args.token, args.days, args.threshold)


if __name__ == "__main__":
    main()
