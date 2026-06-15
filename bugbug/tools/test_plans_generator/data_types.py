# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from dataclasses import dataclass


@dataclass(frozen=True)
class TestPlanGenerationResult:
    test_cases: str
    test_steps: str | None = None
