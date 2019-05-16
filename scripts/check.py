# -*- coding: utf-8 -*-

import argparse
import sys
from logging import INFO, basicConfig, getLogger

from bugbug.models.component import ComponentModel

basicConfig(level=INFO)
logger = getLogger(__name__)


class ModelChecker:
    def go(self, model_name):
        # TODO: Stop hard-coding them
        valid_models = ["component"]

        if model_name not in valid_models:
            exception = f"Invalid model {model_name!r} name, use one of {valid_models!r} instead"
            raise ValueError(exception)

        # TODO: What is the standard file path of the models?
        model_file_name = f"{model_name}model"

        if model_name == "component":
            model_class = ComponentModel
        else:
            # We shouldn't be here
            raise Exception("valid_models is likely not up-to-date anymore")

        # Load the model
        model = model_class.load(model_file_name)

        # Then call the check method of the model
        success = model.check()

        if not success:
            msg = f"Check of model {model_class!r} failed, check the output for reasons why"
            logger.warning(msg)
            sys.exit(1)


def main():
    description = "Check the models"
    parser = argparse.ArgumentParser(description=description)

    parser.add_argument("model", help="Which model to check.")

    args = parser.parse_args()

    checker = ModelChecker()
    checker.go(args.model)
