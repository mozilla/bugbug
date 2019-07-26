# -*- coding: utf-8 -*-

import argparse
import json
import os
from logging import INFO, basicConfig, getLogger

import zstandard

from bugbug import bugzilla, db, model, repository
from bugbug.models import get_model_class
<<<<<<< HEAD
from bugbug.utils import CustomJsonEncoder, zstd_compress
||||||| merged common ancestors
from bugbug.utils import CustomJsonEncoder
=======
from bugbug.utils import CustomJsonEncoder, compress
>>>>>>> e1d526ad03385156f14fc5b2dcf7d999a5936192

basicConfig(level=INFO)
logger = getLogger(__name__)


class Trainer(object):
    

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
            db.download(bugzilla.BUGS_DB, force=True)

        if isinstance(model_obj, model.CommitModel):
            db.download(repository.COMMITS_DB, force=True)

        logger.info(f"Training *{model_name}* model")
        metrics = model_obj.train()

        # Save the metrics as a file that can be uploaded as an artifact.
        metric_file_path = "metrics.json"
        with open(metric_file_path, "w") as metric_file:
            json.dump(metrics, metric_file, cls=CustomJsonEncoder)

        logger.info(f"Training done")

        model_file_name = f"{model_name}model"
        assert os.path.exists(model_file_name)
<<<<<<< HEAD
        zstd_compress(model_file_name)
||||||| merged common ancestors
        self.compress_file(model_file_name)
=======
        compress(model_file_name)
>>>>>>> e1d526ad03385156f14fc5b2dcf7d999a5936192

        logger.info(f"Model compressed")


def main():
    description = "Train the models"
    parser = argparse.ArgumentParser(description=description)

    parser.add_argument("model", help="Which model to train.")

    args = parser.parse_args()

    retriever = Trainer()
    retriever.go(args.model)
