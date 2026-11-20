# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""The models served by the HTTP service.

They are listed here rather than in bugbug_http.models, which the web processes don't import as it
loads the ML libraries.
"""

from bugbug_http import jobs

MODELS_NAMES = [
    "defectenhancementtask",
    "component",
    "componentspecific",
    "invalidcompatibilityreport",
    "needsdiagnosis",
    "regression",
    "stepstoreproduce",
    "spambug",
    "testlabelselect",
    "testgroupselect",
    "testconfigselectvalue",
    "accessibility",
    "performancebug",
    "worksforme",
    "fenixcomponent",
    "perfregressionpredictor",
    "regressor",
]

# The models in MODELS_NAMES which classify comments (subclasses of bugbug.model.CommentModel).
COMMENT_MODELS: list[str] = []

# Models that classify a push (branch + revision) rather than a bug or an issue, keyed by the model
# name accepted by the push prediction endpoint, with their job function (see bugbug_http.jobs).
PUSH_CLASSIFIERS = {
    "perfregressionpredictor": jobs.classify_perf_regression,
}

# Models that classify single commits (see classify_commits).
COMMIT_MODELS = ["regressor"]
