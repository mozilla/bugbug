# -*- coding: utf-8 -*-

import argparse
import datetime
import lzma
import os
import shutil
from logging import INFO, basicConfig, getLogger
from urllib.request import urlretrieve

import taskcluster

from bugbug import get_bugbug_version, model
from bugbug.models import get_model_class
from bugbug.utils import get_taskcluster_options

basicConfig(level=INFO)
logger = getLogger(__name__)

BASE_URL = "https://index.taskcluster.net/v1/task/project.relman.bugbug.data_{}.{}/artifacts/public"
INDEX_URI = "project.relman.bugbug.tracking_metrics.{}.{}"
INDEX_DATE_FORMAT = "%Y.%m.%d.%H.%M.%S"


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
        formatted_base_url = BASE_URL.format(db_type, f"v{get_bugbug_version()}")
        url = f"{formatted_base_url}/{db_type}.json.xz"
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
        metrics = model_obj.train()

        task_id = os.environ.get("TASK_ID")

        # Save the metrics in taskcluster if we are running in task_cluster
        if task_id:
            data = {"metrics": metrics}
            payload = {
                "data": data,
                "taskId": task_id,
                "rank": 0,
                "expires": taskcluster.fromNow("1 year"),
            }

            index = taskcluster.Index(get_taskcluster_options())

            now = datetime.datetime.utcnow()
            date_uri = INDEX_URI.format(model_name, now.strftime(INDEX_DATE_FORMAT))
            latest_uri = INDEX_URI.format(model_name, "latest")

            index.insertTask(date_uri, payload=payload)
            print(f"Tracking metrics inserted at {date_uri}")
            index.insertTask(latest_uri, payload=payload)
            print(f"Tracking metrics inserted at {latest_uri}")

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
