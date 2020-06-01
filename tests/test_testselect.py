# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import pickle

from bugbug import test_scheduling
from bugbug.models.testselect import TestLabelSelectModel


def test_reduce():
    failing_together = test_scheduling.get_failing_together_db("label")
    failing_together[b"test-linux1804-64/debug"] = pickle.dumps(
        {
            "test-windows10/debug": (0.1, 1.0),
            "test-windows10/opt": (0.1, 1.0),
            "test-linux1804-64/opt": (0.1, 1.0),
        }
    )
    failing_together[b"test-linux1804-64/opt"] = pickle.dumps(
        {"test-windows10/opt": (0.1, 0.91),}
    )
    failing_together[b"test-linux1804-64-asan/debug"] = pickle.dumps(
        {"test-linux1804-64/debug": (0.1, 1.0),}
    )
    test_scheduling.close_failing_together_db("label")

    model = TestLabelSelectModel()
    assert model.reduce({"test-linux1804-64/debug", "test-windows10/debug"}, 1.0) == {
        "test-linux1804-64/debug"
    }
    assert model.reduce({"test-linux1804-64/debug", "test-windows10/opt"}, 1.0) == {
        "test-linux1804-64/debug"
    }
    assert model.reduce({"test-linux1804-64/opt", "test-windows10/opt"}, 1.0) == {
        "test-linux1804-64/opt",
        "test-windows10/opt",
    }
    assert model.reduce({"test-linux1804-64/opt", "test-windows10/opt"}, 0.9) == {
        "test-linux1804-64/opt"
    }
    assert model.reduce({"test-linux1804-64/opt", "test-linux1804-64/debug"}, 1.0) == {
        "test-linux1804-64/opt"
    }
    assert model.reduce(
        {"test-linux1804-64-asan/debug", "test-linux1804-64/debug"}, 1.0
    ) == {"test-linux1804-64/debug"}
