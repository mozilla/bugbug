# -*- coding: utf-8 -*-
import importlib
import logging
from typing import Type

from bugbug.model import Model

LOGGER = logging.getLogger()


MODELS = {
    "accessibility": "bugbug.models.accessibility.AccessibilityModel",
    "annotateignore": "bugbug.models.annotate_ignore.AnnotateIgnoreModel",
    "assignee": "bugbug.models.assignee.AssigneeModel",
    "backout": "bugbug.models.backout.BackoutModel",
    "browsername": "bugbug.models.browsername.BrowserNameModel",
    "bugtype": "bugbug.models.bugtype.BugTypeModel",
    "component": "bugbug.models.component.ComponentModel",
    "defect": "bugbug.models.defect.DefectModel",
    "defectenhancementtask": "bugbug.models.defect_enhancement_task.DefectEnhancementTaskModel",
    "devdocneeded": "bugbug.models.devdocneeded.DevDocNeededModel",
    "fixtime": "bugbug.models.fixtime.FixTimeModel",
    "invalidcompatibilityreport": "bugbug.models.invalid_compatibility_report.InvalidCompatibilityReportModel",
    "needsdiagnosis": "bugbug.models.needsdiagnosis.NeedsDiagnosisModel",
    "performancebug": "bugbug.models.performancebug.PerformanceBugModel",
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
    "worksforme": "bugbug.models.worksforme.WorksForMeModel",
    "fenixcomponent": "bugbug.models.fenixcomponent.FenixComponentModel",
}


def get_model_class(model_name: str) -> Type[Model]:
    if model_name not in MODELS:
        err_msg = f"Invalid name {model_name}, not in {list(MODELS.keys())}"
        raise ValueError(err_msg)

    full_qualified_class_name = MODELS[model_name]
    module_name, class_name = full_qualified_class_name.rsplit(".", 1)

    module = importlib.import_module(module_name)

    return getattr(module, class_name)
