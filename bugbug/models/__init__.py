# -*- coding: utf-8 -*-
import importlib
import logging

LOGGER = logging.getLogger()


MODELS = {
    "assignee": "bugbug.models.assignee.AssigneeModel",
    "backout": "bugbug.models.backout.BackoutModel",
    "bug": "bugbug.model.BugModel",
    "bugtype": "bugbug.models.bugtype.BugTypeModel",
    "component": "bugbug.models.component.ComponentModel",
    "component_nn": "bugbug.models.component_nn.ComponentNNModel",
    "defect": "bugbug.models.defect.DefectModel",
    "defectenhancementtask": "bugbug.models.defect_enhancement_task.DefectEnhancementTaskModel",
    "devdocneeded": "bugbug.models.devdocneeded.DevDocNeededModel",
    "duplicate": "bugbug.models.duplicate.DuplicateModel",
    "qaneeded": "bugbug.models.qaneeded.QANeededModel",
    "rcatype": "bugbug.models.rcatype.RCATypeModel",
    "regression": "bugbug.models.regression.RegressionModel",
    "regressionrange": "bugbug.models.regressionrange.RegressionRangeModel",
    "regressor": "bugbug.models.regressor.RegressorModel",
    "spambug": "bugbug.models.spambug.SpamBugModel",
    "stepstoreproduce": "bugbug.models.stepstoreproduce.StepsToReproduceModel",
    "testlabelselect": "bugbug.models.testselect.TestLabelSelectModel",
    "testgroupselect": "bugbug.models.testselect.TestGroupSelectModel",
    "testconfiggroupselect": "bugbug.models.testselect.TestConfigGroupSelectModel",
    "testfailure": "bugbug.models.testfailure.TestFailureModel",
    "tracking": "bugbug.models.tracking.TrackingModel",
    "uplift": "bugbug.models.uplift.UpliftModel",
}


def load_model_class(full_qualified_class_name):
    """ Load the class dynamically in order to speed up the boot and allow for
    dynamic optional dependencies to be declared and check at import time
    """
    module_name, class_name = full_qualified_class_name.rsplit(".", 1)

    module = importlib.import_module(module_name)

    return getattr(module, class_name)


def get_model_class(model_name):
    if model_name not in MODELS:
        err_msg = f"Invalid name {model_name}, not in {list(MODELS.keys())}"
        raise ValueError(err_msg)

    full_qualified_class_name = MODELS[model_name]
    return load_model_class(full_qualified_class_name)


def load_model(model_name):
    model_class = get_model_class(model_name)

    model_file_path = f"{model_name}model"

    LOGGER.debug(f"Lookup model in {model_file_path}")
    return model_class.load(model_file_path)
