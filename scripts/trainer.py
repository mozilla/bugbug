# -*- coding: utf-8 -*-

import argparse
import lzma
import os
import shutil
from logging import INFO, basicConfig, getLogger
from urllib.request import urlretrieve

from bugbug import model
from bugbug.models import get_model_class

basicConfig(level=INFO)
logger = getLogger(__name__)

BASE_URL = "https://index.taskcluster.net/v1/task/project.relman.bugbug.data_{}.latest/artifacts/public"


class Trainer(object):
    def decompress_file(self, path):
        with lzma.open(f"{path}.xz", "rb") as input_f:
            with open(path, "wb") as output_f:
                shutil.copyfileobj(input_f, output_f)
        assert os.path.exists(path), "Decompressed file exists"

    def compress_file(self, path):
        with open(path, "rb") as input_f:
            with lzma.open(f"{path}.xz", "wb") as output_f:
                shutil.copyfileobj(input_f, output_f)

    def download_db(self, db_type):
        path = f"data/{db_type}.json"
        url = f"{BASE_URL.format(db_type)}/{db_type}.json.xz"
        logger.info(f"Downloading {db_type} database from {url} to {path}.xz")
        urlretrieve(url, f"{path}.xz")
        assert os.path.exists(f"{path}.xz"), "Downloaded file exists"
        logger.info(f"Decompressing {db_type} database")
        self.decompress_file(path)

    def go(self, model_name):
        # Download datasets that were built by bugbug_data.
        os.makedirs("data", exist_ok=True)

        model_class = get_model_class(model_name)

        if issubclass(model_class, model.BugModel) or issubclass(
            model_class, model.BugCoupleModel
        ):
            self.download_db("bugs")

        if issubclass(model_class, model.CommitModel):
            self.download_db("commits")

        logger.info(f"Training *{model_name}* model")

        model_obj = model_class()
        model_obj.train()

        logger.info(f"Training done")

        model_file_name = f"{model_name}model"
        assert os.path.exists(model_file_name)
        self.compress_file(model_file_name)

        logger.info(f"Model compressed")


def main():
    description = "Train the models"
    parser = argparse.ArgumentParser(description=description)

    parser.add_argument("model", help="Which model to train.")

    args = parser.parse_args()

    retriever = Trainer()
    retriever.go(args.model)
