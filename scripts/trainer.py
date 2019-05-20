# -*- coding: utf-8 -*-

import argparse
import lzma
import os
import shutil
from logging import INFO, basicConfig, getLogger
from urllib.request import urlretrieve

from bugbug.models import get_model_class

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

    def go(self, model_name):
        # Download datasets that were built by bugbug_data.
        os.makedirs("data", exist_ok=True)

        # Bugs.json
        logger.info("Downloading bugs database")
        bugs_url = BASE_URL.format("bugs")
        urlretrieve(f"{bugs_url}/bugs.json.xz", "data/bugs.json.xz")
        logger.info("Decompressing bugs database")
        self.decompress_file("data/bugs.json")

        logger.info(f"Training *{model_name}* model")

        model_class = get_model_class(model_name)
        model = model_class()
        model.train()

        model_file_name = f"{model_name}model"
        self.compress_file(model_file_name)


def main():
    description = "Train the models"
    parser = argparse.ArgumentParser(description=description)

    parser.add_argument("model", help="Which model to train.")

    args = parser.parse_args()

    retriever = Trainer()
    retriever.go(args.model)
