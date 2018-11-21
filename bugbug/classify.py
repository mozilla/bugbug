# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from sklearn.externals import joblib

from bugbug import bugzilla

extraction_pipeline = None
clf = None


def init(model):
    global extraction_pipeline, clf
    extraction_pipeline, clf = joblib.load(model)


def classify(bugs):
    assert bugs is not None
    assert extraction_pipeline is not None and clf is not None, 'The module needs to be initialized first'

    if not isinstance(bugs, list):
        bugs = [bugs]

    if isinstance(bugs[0], int) or isinstance(bugs[0], str):
        bugs = list(bugzilla.download_bugs([int(i) for i in bugs]))

    X = extraction_pipeline.transform(bugs)
    return clf.predict(X)
