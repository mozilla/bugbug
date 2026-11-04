# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import pytest

from bugbug.model_calibration import fit_isotonic_breakpoints, interpolate_breakpoints


def test_isotonic_breakpoints_pool_violators_and_interpolate() -> None:
    # The rate dips at 0.6, so 0.6 and 0.7 are pooled by weight: (20 + 15) / 2000.
    x, y = fit_isotonic_breakpoints(
        [0.5, 0.6, 0.7, 0.95], [0.01, 0.02, 0.015, 0.3], [1000, 1000, 1000, 100]
    )
    assert x[0] == 0.5 and x[-1] == 0.95
    assert (
        y == sorted(y) and y[0] == pytest.approx(0.01) and y[-1] == pytest.approx(0.3)
    )
    assert interpolate_breakpoints(0.6, x, y) == pytest.approx(0.0175)
    assert interpolate_breakpoints(0.7, x, y) == pytest.approx(0.0175)
    # Beyond the breakpoints the ends hold; between two, the line between them.
    assert interpolate_breakpoints(0.1, x, y) == pytest.approx(0.01)
    assert interpolate_breakpoints(1.0, x, y) == pytest.approx(0.3)
    between = interpolate_breakpoints([0.825], x, y)
    assert 0.0175 < between[0] < 0.3
