# -*- coding: utf-8 -*-
import logging
import os

from bugbug.model import BugModel
from bugbug.models.component import ComponentModel
from bugbug.models.defect_enhancement_task import DefectEnhancementTaskModel
from bugbug.models.regression import RegressionModel

LOGGER = logging.getLogger()


MODELS = {
    "defectenhancementtask": DefectEnhancementTaskModel,
    "component": ComponentModel,
    "regression": RegressionModel,
    "bug": BugModel,
}


def load_model(model_name, model_dir=None):
    if model_dir is None:
        model_dir = "."

    model_file_path = os.path.join(model_dir, f"{model_name}model")
    LOGGER.info(f"Lookup model in {model_file_path}")
    model = MODELS[model_name].load(model_file_path)
    return model
