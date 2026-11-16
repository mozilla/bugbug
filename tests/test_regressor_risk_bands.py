# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from collections import Counter

import numpy as np

from bugbug.models.regressor import RegressorModel, compute_risk_bands


def test_compute_risk_bands():
    risks = np.random.RandomState(0).uniform(size=10000)

    risk_bands = compute_risk_bands(risks)

    assert [name for name, _, _ in risk_bands] == ["l", "a", "h", "v"]
    # The bands are contiguous and cover the whole risk scale.
    assert risk_bands[0][1] == 0.0
    assert risk_bands[-1][2] == 1.0
    for (_, _, end), (_, start, _) in zip(risk_bands, risk_bands[1:]):
        assert end == start

    model = RegressorModel()
    model.risk_bands = risk_bands
    counts = Counter(model.find_risk_band(risk) for risk in risks)
    assert counts == {"l": 5000, "a": 3000, "h": 1500, "v": 500}

    # Boundaries belong to the lower band.
    assert model.find_risk_band(risk_bands[0][2]) == "l"
    assert model.find_risk_band(0.0) == "l"
    assert model.find_risk_band(1.0) == "v"


def test_compute_risk_bands_ties():
    # Isotonic calibration outputs few distinct values, so many risks are tied.
    risks = np.repeat([0.1, 0.2, 0.5, 0.9], [600, 250, 120, 30])

    risk_bands = compute_risk_bands(risks)

    model = RegressorModel()
    model.risk_bands = risk_bands
    counts = Counter(model.find_risk_band(risk) for risk in risks)
    # Each tied group goes to the band whose target share it fits best, and the top
    # band is not left empty even if the 95th percentile falls in a tied group.
    assert counts == {"l": 600, "a": 250, "h": 120, "v": 30}
