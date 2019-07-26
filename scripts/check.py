# -*- coding: utf-8 -*-

import argparse
import os
import sys
from logging import INFO, basicConfig, getLogger
from urllib.request import urlretrieve

import zstandard

from bugbug.models import load_model
<<<<<<< HEAD
from bugbug import utils
||||||| merged common ancestors

=======
from bugbug.utils import decompress
>>>>>>> e1d526ad03385156f14fc5b2dcf7d999a5936192
basicConfig(level=INFO)
logger = getLogger(__name__)


def download_model(model_url, file_path):
    logger.info(f"Downloading model from {model_url!r} and save it in {file_path!r}")
    urlretrieve(model_url, f"{file_path}.zst")

<<<<<<< HEAD
    utils.zstd_decompress(file_path)
    logger.info(f"Written model in {file_path}")
||||||| merged common ancestors
    dctx = zstandard.ZstdDecompressor()
    with open(f"{file_path}.zst", "rb") as input_f:
        with open(file_path, "wb") as output_f:
            dctx.copy_stream(input_f, output_f)
            logger.info(f"Written model in {file_path}")
=======
    decompress(file_path)
    logger.info(f"Written model in {file_path}")
>>>>>>> e1d526ad03385156f14fc5b2dcf7d999a5936192


class ModelChecker:
    def go(self, model_name):
        should_download_model = bool(os.getenv("SHOULD_DOWNLOAD_MODEL"))
        download_url = os.getenv("MODEL_DOWNLOAD_URL")

        if should_download_model and download_url:
            download_model(download_url, f"{model_name}model")

        # Load the model
        model = load_model(model_name)

        # Then call the check method of the model
        success = model.check()

        if not success:
            msg = f"Check of model {model.__class__!r} failed, check the output for reasons why"
            logger.warning(msg)
            sys.exit(1)


def main():
    description = "Check the models"
    parser = argparse.ArgumentParser(description=description)

    parser.add_argument("model", help="Which model to check.")

    args = parser.parse_args()

    checker = ModelChecker()
    checker.go(args.model)
