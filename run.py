# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import csv
import os
from datetime import datetime, timedelta

import numpy as np

from bugbug import repository  # noqa
from bugbug import bugzilla, db

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--lemmatization",
        help="Perform lemmatization (using spaCy)",
        action="store_true",
    )
    parser.add_argument("--train", help="Perform training", action="store_true")
    parser.add_argument(
        "--goal",
        help="Goal of the classifier",
        choices=[
            "bug",
            "regression",
            "tracking",
            "qaneeded",
            "uplift",
            "component",
            "devdocneeded",
            "defectenhancementtask",
            "assignee",
        ],
        default="bug",
    )
    parser.add_argument(
        "--classifier",
        help="Type of the classifier",
        choices=["default", "nn"],
        default="default",
    )
    parser.add_argument("--classify", help="Perform evaluation", action="store_true")
    parser.add_argument(
        "--generate-sheet",
        help="Perform evaluation on bugs from last week and generate a csv file",
        action="store_true",
    )
    parser.add_argument("--token", help="Bugzilla token", action="store")
    parser.add_argument("--old", help="Analyze old bugs", action="store_true")
    args = parser.parse_args()

    model_file_name = "{}{}model".format(
        args.goal, "" if args.classifier == "default" else args.classifier
    )

    if args.goal == "bug":
        from bugbug.models.bug import BugModel

        model_class = BugModel
    elif args.goal == "defectenhancementtask":
        from bugbug.models.defect_enhancement_task import DefectEnhancementTaskModel

        model_class = DefectEnhancementTaskModel
    elif args.goal == "regression":
        from bugbug.models.regression import RegressionModel

        model_class = RegressionModel
    elif args.goal == "tracking":
        from bugbug.models.tracking import TrackingModel

        model_class = TrackingModel
    elif args.goal == "qaneeded":
        from bugbug.models.qaneeded import QANeededModel

        model_class = QANeededModel
    elif args.goal == "uplift":
        from bugbug.models.uplift import UpliftModel

        model_class = UpliftModel
    elif args.goal == "component":
        if args.classifier == "default":
            from bugbug.models.component import ComponentModel

            model_class = ComponentModel
        elif args.classifier == "nn":
            from bugbug.models.component_nn import ComponentNNModel

            model_class = ComponentNNModel
    elif args.goal == "devdocneeded":
        from bugbug.models.devdocneeded import DevDocNeededModel

        model_class = DevDocNeededModel
    elif args.goal == "assignee":
        from bugbug.models.assignee import AssigneeModel

        model_class = AssigneeModel

    if args.train:
        db.download()
        if args.old and model_class == BugModel:
            model = model_class(args.lemmatization, args.old)
        else:
            model = model_class(args.lemmatization)
        model.train()
    else:
        model = model_class.load(model_file_name)

    if args.classify:
        for bug in bugzilla.get_bugs():
            print(
                f'https://bugzilla.mozilla.org/show_bug.cgi?id={ bug["id"] } - { bug["summary"]} '
            )

            if model.calculate_importance:
                probas, importances = model.classify(
                    bug, probabilities=True, importances=True
                )

                feature_names = model.get_feature_names()
                for i, (importance, index, is_positive) in enumerate(importances):
                    print(
                        f'{i + 1}. \'{feature_names[int(index)]}\' ({"+" if (is_positive) else "-"}{importance})'
                    )
            else:
                probas = model.classify(bug, probabilities=True, importances=False)

            if np.argmax(probas) == 1:
                print(f"Positive! {probas}")
            else:
                print(f"Negative! {probas}")
            input()

    if args.generate_sheet:
        assert (
            args.token is not None
        ), "A Bugzilla token should be set in order to download bugs"
        today = datetime.utcnow()
        a_week_ago = today - timedelta(7)
        bugzilla.set_token(args.token)
        bugs = bugzilla.download_bugs_between(a_week_ago, today)

        print(f"Classifying {len(bugs)} bugs...")

        rows = [["Bug", f"{args.goal}(model)", args.goal, "Title"]]

        for bug in bugs:
            p = model.classify(bug, probabilities=True)
            rows.append(
                [
                    f'https://bugzilla.mozilla.org/show_bug.cgi?id={bug["id"]}',
                    "y" if p[0][1] >= 0.7 else "n",
                    "",
                    bug["summary"],
                ]
            )

        os.makedirs("sheets", exist_ok=True)
        with open(
            os.path.join(
                "sheets",
                f'{args.goal}-{datetime.utcnow().strftime("%Y-%m-%d")}-labels.csv',
            ),
            "w",
        ) as f:
            writer = csv.writer(f)
            writer.writerows(rows)
