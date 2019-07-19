# -*- coding: utf-8 -*-

import argparse
import json
import os
from logging import INFO, basicConfig, getLogger

import zstandard

from bugbug import db, get_bugbug_version, model
from bugbug.models import get_model_class
from bugbug.utils import CustomJsonEncoder

basicConfig(level=INFO)
logger = getLogger(__name__)

BASE_URL = "https://index.taskcluster.net/v1/task/project.relman.bugbug.data_{}.{}/artifacts/public"


class Trainer(object):
    def compress_file(self, path):
        cctx = zstandard.ZstdCompressor()
        with open(path, "rb") as input_f:
            with open(f"{path}.zst", "wb") as output_f:
                cctx.copy_stream(input_f, output_f)

    def download_db(self, db_type):
        path = f"data/{db_type}.json"
        formatted_base_url = BASE_URL.format(db_type, f"v{get_bugbug_version()}")
        url = f"{formatted_base_url}/{db_type}.json.zst"
        db.register(path, url, get_bugbug_version())
        db.download(path, force=True, support_files_too=True)

    def go(self, model_name):
        # Download datasets that were built by bugbug_data.
        os.makedirs("data", exist_ok=True)

        model_class = get_model_class(model_name)
        model_obj = model_class()

        if (
            isinstance(model_obj, model.BugModel)
            or isinstance(model_obj, model.BugCoupleModel)
            or (hasattr(model_obj, "bug_data") and model_obj.bug_data)
        ):
            self.download_db("bugs")

        if isinstance(model_obj, model.CommitModel):
            self.download_db("commits")

        logger.info(f"Training *{model_name}* model")
        metrics = model_obj.train()

        # Save the metrics as a file that can be uploaded as an artifact.
        metric_file_path = "metrics.json"
        with open(metric_file_path, "w") as metric_file:
            json.dump(metrics, metric_file, cls=CustomJsonEncoder)

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
