# Copyright 2026 Mozilla Foundation
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import pytest


@pytest.fixture
def row():
    return {
        "id": 265000,
        "is_regression": True,
        "series_signature": {
            "id": 5690997,
            "framework_id": 13,
            "machine_platform": "linux2404-64-shippable",
            "suite": "speedometer3",
            "test": "NewsSite-Next/total",
            "lower_is_better": True,
            "option_collection_hash": "opt",
            "extra_options": ["fission", "webrender"],
            "measurement_unit": "ms",
        },
    }
