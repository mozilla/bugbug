# -*- coding: utf-8 -*-

import argparse
import inspect
import json
import os
import sys
from logging import INFO, basicConfig, getLogger

import taskcluster

from bugbug import db
from bugbug.models import MODELS, get_model_class
from bugbug.utils import CustomJsonEncoder, zstd_compress

MODELS_WITH_TYPE = ("component",)

basicConfig(level=INFO)
logger = getLogger(__name__)

TASKCLUSTER_DEFAULT_URL = "https://community-tc.services.mozilla.com"


def get_taskcluster_options():
    """Get the Taskcluster setup options according to current environment.

    The current environment could be local.
    """
    options = taskcluster.optionsFromEnvironment()
    proxy_url = os.environ.get("TASKCLUSTER_PROXY_URL")

    if proxy_url is not None:
        # Always use proxy url when available
        options["rootUrl"] = proxy_url

    if "rootUrl" not in options:
        # Always have a value in root url
        options["rootUrl"] = TASKCLUSTER_DEFAULT_URL

    return options


def create_task(task_definition):
    options = get_taskcluster_options()
    queue = taskcluster.Queue(options)

    task_id = taskcluster.utils.slugId()

    try:
        queue.createTask(task_id, task_definition)
        logger.info(
            "Task created at https://community-tc.services.mozilla.com/tasks/%s",
            task_id,
        )
    except taskcluster.exceptions.TaskclusterAuthFailure:
        logger.exception("Failed to authenticate with Taskcluster")
        raise


class Trainer(object):
    def go(self, args):
        # Download datasets that were built by bugbug_data.
        os.makedirs("data", exist_ok=True)

        if args.classifier != "default":
            assert (
                args.model in MODELS_WITH_TYPE
            ), f"{args.classifier} is not a valid classifier type for {args.model}"

            model_name = f"{args.model}_{args.classifier}"
        else:
            model_name = args.model

        model_class = get_model_class(model_name)
        parameter_names = set(inspect.signature(model_class.__init__).parameters)
        parameters = {
            key: value for key, value in vars(args).items() if key in parameter_names
        }
        model_obj = model_class(**parameters)

        if args.download_db:
            for required_db in model_obj.training_dbs:
                assert db.download(required_db)

            if args.download_eval:
                model_obj.download_eval_dbs()
        else:
            logger.info("Skipping download of the databases")

        logger.info("Training *%s* model", model_name)
        metrics = model_obj.train(limit=args.limit)

        # Save the metrics as a file that can be uploaded as an artifact.
        metric_file_path = "metrics.json"
        with open(metric_file_path, "w") as metric_file:
            json.dump(metrics, metric_file, cls=CustomJsonEncoder)

        logger.info("Training done")

        model_file_name = f"{model_name}model"
        assert os.path.exists(model_file_name)
        zstd_compress(model_file_name)

        logger.info("Model compressed")

        if model_obj.store_dataset:
            assert os.path.exists(f"{model_file_name}_data_X")
            zstd_compress(f"{model_file_name}_data_X")
            assert os.path.exists(f"{model_file_name}_data_y")
            zstd_compress(f"{model_file_name}_data_y")


def parse_args(args):
    description = "Train the models"
    main_parser = argparse.ArgumentParser(description=description)

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--limit",
        type=int,
        help="Only train on a subset of the data, used mainly for integrations tests",
    )
    parser.add_argument(
        "--no-download",
        action="store_false",
        dest="download_db",
        help="Do not download databases, uses whatever is on disk",
    )
    parser.add_argument(
        "--download-eval",
        action="store_true",
        dest="download_eval",
        help="Download databases and database support files required at runtime (e.g. if the model performs custom evaluations)",
    )
    parser.add_argument(
        "--lemmatization",
        help="Perform lemmatization (using spaCy)",
        action="store_true",
    )
    parser.add_argument(
        "--classifier",
        help="Type of the classifier. Only used for component classification.",
        choices=["default", "nn"],
        default="default",
    )
    parser.add_argument(
        "--taskcluster",
        action="store_true",
        help="Train the model using taskcluster",
    )

    subparsers = main_parser.add_subparsers(title="model", dest="model", required=True)

    for model_name in MODELS:
        subparser = subparsers.add_parser(
            model_name, parents=[parser], help=f"Train {model_name} model"
        )

        try:
            model_class_init = get_model_class(model_name).__init__
        except ImportError:
            continue

        for parameter in inspect.signature(model_class_init).parameters.values():
            if parameter.name == "self":
                continue

            # Skip parameters handled by the base class (TODO: add them to the common argparser and skip them automatically without hardcoding by inspecting the base class)
            if parameter.name == "lemmatization":
                continue

            parameter_type = parameter.annotation
            if parameter_type == inspect._empty:
                parameter_type = type(parameter.default)
            assert parameter_type is not None

            if parameter_type == bool:
                subparser.add_argument(
                    f"--{parameter.name}"
                    if parameter.default is False
                    else f"--no-{parameter.name}",
                    action="store_true"
                    if parameter.default is False
                    else "store_false",
                    dest=parameter.name,
                )
            else:
                subparser.add_argument(
                    f"--{parameter.name}",
                    default=parameter.default,
                    dest=parameter.name,
                    type=int,
                )

    return main_parser.parse_args(args)


def main():
    args = parse_args(sys.argv[1:])

    if args.train_with_taskcluster:
        task_definition = {
            "payload": {
                "command": [
                    "python",
                    "-m",
                    "scripts.trainer",
                    args.model,
                    "--limit",
                    str(args.limit),
                    "--no-download" if not args.download_db else "",
                    "--download-eval" if args.download_eval else "",
                    "--lemmatization" if args.lemmatization else "",
                    "--classifier",
                    args.classifier,
                ],
                "maxRunTime": 25200,
                "env": {
                    "TAG": "test",  # This will be changed
                },
            },
        }
        create_task(task_definition)
    else:
        retriever = Trainer()
        retriever.go(args)


if __name__ == "__main__":
    main()
