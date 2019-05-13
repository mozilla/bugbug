# -*- coding: utf-8 -*-
import logging
import os

from bugbug.model import BugModel
from bugbug.models.assignee import AssigneeModel
from bugbug.models.backout import BackoutModel
from bugbug.models.component import ComponentModel
from bugbug.models.component_nn import ComponentNNModel
from bugbug.models.defect import DefectModel
from bugbug.models.defect_enhancement_task import DefectEnhancementTaskModel
from bugbug.models.devdocneeded import DevDocNeededModel
from bugbug.models.qaneeded import QANeededModel
from bugbug.models.regression import RegressionModel
from bugbug.models.tracking import TrackingModel
from bugbug.models.uplift import UpliftModel

LOGGER = logging.getLogger()


MODELS = {
    "assignee": AssigneeModel,
    "backout": BackoutModel,
    "bug": BugModel,
    "component": ComponentModel,
    "component_nn": ComponentNNModel,
    "defect": DefectModel,
    "defectenhancementtask": DefectEnhancementTaskModel,
    "devdocneeded": DevDocNeededModel,
    "qaneeded": QANeededModel,
    "regression": RegressionModel,
    "tracking": TrackingModel,
    "uplift": UpliftModel,
}


def get_model_class(model_name):
    if model_name not in MODELS:
        err_msg = f"Invalid name {model_name}, not in {list(MODELS.keys())}"
        raise ValueError(err_msg)

    return MODELS[model_name]


def load_model(model_name, model_dir=None):
    model_class = get_model_class(model_name)

    if model_dir is None:
        model_dir = "."

    model_file_path = os.path.join(model_dir, f"{model_name}model")

    LOGGER.info(f"Lookup model in {model_file_path}")
    model = model_class.load(model_file_path)
    return model
