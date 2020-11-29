# -*- coding: utf-8 -*-

import argparse
import sys
from logging import INFO, basicConfig, getLogger

from bugbug.model import Model
from bugbug.utils import download_model

basicConfig(level=INFO)
logger = getLogger(__name__)


class ModelChecker:
    def go(self, model_name: str) -> None:
        # Load the model
        model = Model.load(download_model(model_name))

        # Then call the check method of the model
        success = model.check()

        if not success:
            msg = f"Check of model {model.__class__!r} failed, check the output for reasons why"
            logger.warning(msg)
            sys.exit(1)


def main() -> None:
    description = "Check the models"
    parser = argparse.ArgumentParser(description=description)

    parser.add_argument("model", help="Which model to check.")

    args = parser.parse_args()

    checker = ModelChecker()
    checker.go(args.model)


if __name__ == "__main__":
    main()
