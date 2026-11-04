# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Performance alerts data pipeline.

Extracts Perfherder alerts, pushes and perf job coverage from the Treeherder
replica exposed by STMO, task costs from BigQuery ``fxci`` and regression bugs
from Bugzilla, and derives labeled datasets for performance test selection and
performance regression prediction. Importing ``bugbug.perf.datasets`` registers
every dataset with ``bugbug.db``; see ``docs/perf-alerts.md`` for the modules.
"""
