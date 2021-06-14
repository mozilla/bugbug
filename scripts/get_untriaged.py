# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import csv
import json
import os
import sys
from datetime import date, datetime, timedelta

# Inject project path
sys.path.append("../")


def fetch_untriaged(args):
    from bugbug import bugzilla

    # Set bugzilla token and download bugs
    bugzilla.set_token(args.token)
    bug_ids = bugzilla.get_ids_between(date.today() - timedelta(days=args.days_back))
    bugs = bugzilla.get(bug_ids)

    # Get untriaged bugs
    untriaged_bugs = []
    for bug in bugs.values():
        for history in bug["history"]:
            for change in history["changes"]:
                if (
                    change["field_name"] == "component"
                    and change["removed"] == "Untriaged"
                ):
                    untriaged_bugs.append(bug)

    with open("bugs-{}.json".format(datetime.now().strftime("%s")), "w") as f:
        json.dump(untriaged_bugs, f)

    return untriaged_bugs


def run_untriaged(untriaged_bugs):
    from bugbug.models.component import ComponentModel
    from bugbug.models.component_nn import ComponentNNModel

    models = [
        (ComponentModel, "../componentmodel"),
        (ComponentNNModel, "../componentnnmodel"),
    ]
    for (model_class, model_file_name) in models:
        rows = []
        model = model_class.load(model_file_name)
        for bug in untriaged_bugs:
            p = model.classify(bug, probabilities=True)
            url = f'https://bugzilla.mozilla.org/show_bug.cgi?id={bug["id"]}'

            classifiable = p[p >= 0.7].size >= 1
            classification = ""

            expected_component = model.filter_component(
                bug["product"], bug["component"]
            )
            if not expected_component:
                print("Skipping bug: {}".format(bug["id"]))
                continue

            if classifiable:
                print("Classifying bug with ID: {}".format(bug["id"]))
                classification = model.classify(bug)[0]
                print("Classified bug as: {}".format(classification))

            else:
                print("Not classifiable bug: {}".format(bug["id"]))

            correct_prediction = expected_component == classification
            rows.append(
                [
                    url,
                    classifiable,
                    expected_component,
                    classification,
                    correct_prediction,
                    bug["summary"],
                ]
            )

        os.makedirs("sheets", exist_ok=True)
        class_name = model.__class__.__name__
        timestamp = datetime.utcnow().strftime("%Y-%m-%d")
        sheet_name = f"{class_name}-{timestamp}-labels.csv"
        with open(os.path.join("sheets", sheet_name), "w") as f:
            writer = csv.writer(f)
            writer.writerows(rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", help="Bugzilla API token")
    parser.add_argument("--days-back", type=int, help="Days since to fetch bugs")
    parser.add_argument("--file_path", help="Days since to fetch bugs")

    args = parser.parse_args()

    if args.file_path:
        with open(args.file_path, "r") as f:
            bugs = json.load(f)
    else:
        bugs = fetch_untriaged(args)

    run_untriaged(bugs)
