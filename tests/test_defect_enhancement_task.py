# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import pandas as pd

from bugbug.models.defect_enhancement_task import DefectEnhancementTaskModel


def test_get_defect_enhancement_task_labels():
    model = DefectEnhancementTaskModel()
    classes, _ = model.get_labels()
    assert classes[1042414] == "defect"
    assert classes[1531080] == "task"
    assert classes[1348788] == "enhancement"


def test_fixed_defects_are_weighted_less():
    model = DefectEnhancementTaskModel()
    model.train_bug_ids = [1, 2, 3]
    model.fixed_defect_bug_ids = {2}
    X = pd.DataFrame({"title": ["a", "b", "c"]}).iloc[[2, 1]]
    assert list(model.get_sample_weights(X, None)) == [1.0, 0.1]
