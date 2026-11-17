# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Summary statistics shared by the CI replay, the metrics and the reports."""

import random
import statistics
from typing import Sequence


def percentile(values: Sequence[float], q: float) -> float:
    """The value at rank ``floor(q * n)`` of the sorted ``values``; 0.0 when empty."""
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))] if ordered else 0.0


def bootstrap_mean(
    values: Sequence[float], samples: int = 1000, seed: int = 0
) -> tuple[float, float] | None:
    """95 percent percentile bootstrap interval of the mean, None below two values."""
    if len(values) < 2:
        return None
    rng = random.Random(seed)
    means = [
        statistics.fmean(rng.choices(values, k=len(values))) for _ in range(samples)
    ]
    return percentile(means, 0.025), percentile(means, 0.975)
