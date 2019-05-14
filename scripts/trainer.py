# -*- coding: utf-8 -*-

import argparse
import lzma
import os
import shutil
from logging import INFO, basicConfig, getLogger
from urllib.request import urlretrieve

from bugbug.models.component import ComponentModel
from bugbug.models.defect_enhancement_task import DefectEnhancementTaskModel
from bugbug.models.regression import RegressionModel
from bugbug.models.tracking import TrackingModel

basicConfig(level=INFO)
logger = getLogger(__name__)

BASE_URL = "https://index.taskcluster.net/v1/task/project.relman.bugbug.data_{}.latest/artifacts/public"


class Trainer(object):
    def decompress_file(self, path):
        with lzma.open(f"{path}.xz", "rb") as input_f:
            with open(path, "wb") as output_f:
                shutil.copyfileobj(input_f, output_f)

    def compress_file(self, path):
        with open(path, "rb") as input_f:
            with lzma.open(f"{path}.xz", "wb") as output_f:
                shutil.copyfileobj(input_f, output_f)

    def train_defect_enhancement_task(self):
        logger.info("Training *defect vs enhancement vs task* model")
        model = DefectEnhancementTaskModel()
        model.train()
        self.compress_file("defectenhancementtaskmodel")

    def train_component(self):
        logger.info("Training *component* model")
        model = ComponentModel()
        model.train()
        self.compress_file("componentmodel")

    def train_regression(self):
        logger.info("Training *regression vs non-regression* model")
        model = RegressionModel()
        model.train()
        self.compress_file("regressionmodel")

    def train_tracking(self):
        logger.info("Training *tracking* model")
        model = TrackingModel()
        model.train()
        self.compress_file("trackingmodel")

    def go(self, model):
        # TODO: Stop hard-coding them
        valid_models = ["defect", "component", "regression", "tracking"]

        if model not in valid_models:
            exception = (
                f"Invalid model {model!r} name, use one of {valid_models!r} instead"
            )
            raise ValueError(exception)

        # Download datasets that were built by bugbug_data.
        os.makedirs("data", exist_ok=True)

        # Bugs.json
        logger.info("Downloading bugs database")
        bugs_url = BASE_URL.format("bugs")
        urlretrieve(f"{bugs_url}/bugs.json.xz", "data/bugs.json.xz")
        logger.info("Decompressing bugs database")
        self.decompress_file("data/bugs.json")

        if model == "defect":
            # Train classifier for defect-vs-enhancement-vs-task.
            self.train_defect_enhancement_task()
        elif model == "component":
            # Train classifier for the component of a bug.
            self.train_component()
        elif model == "regression":
            # Train classifier for regression-vs-nonregression.
            self.train_regression()
        elif model == "tracking":
            # Train classifier for tracking bugs.
            self.train_tracking()
        else:
            # We shouldn't be here
            raise Exception("valid_models is likely not up-to-date anymore")


def main():
    description = "Train the models"
    parser = argparse.ArgumentParser(description=description)

    parser.add_argument("model", help="Which model to train.")

    args = parser.parse_args()

    retriever = Trainer()
    retriever.go(args.model)
