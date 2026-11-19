# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging
from collections import Counter
from datetime import datetime, timezone

import dateutil.parser
from dateutil.relativedelta import relativedelta

from bugbug import bugzilla
from bugbug.models.component import BaseComponentModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class FenixComponentModel(BaseComponentModel):
    def get_labels(self):
        classes = {}

        date_limit = datetime.now(timezone.utc) - relativedelta(years=2)

        for bug_data in bugzilla.get_bugs():
            # We want the model to be aware of GeckoView bugs, as it is common
            # for bugs filed under Firefox for Android to end up in GeckoView.
            if (
                bug_data["product"] != "Firefox for Android"
                and bug_data["product"] != "GeckoView"
            ):
                continue

            # Exclude 'Firefox for Android::General' because it contains bugs that
            # may belong to other components, thus introducing noise. However,
            # include 'GeckoView::General' because the model should be able to move
            # bugs to GeckoView.
            if (
                bug_data["component"] == "General"
                and bug_data["product"] != "GeckoView"
            ):
                continue

            if dateutil.parser.parse(bug_data["creation_time"]) < date_limit:
                continue

            # Include the product, as some components exist in both products
            # (e.g. Media).
            classes[int(bug_data["id"])] = (
                f"{bug_data['product']}::{bug_data['component']}"
            )

        # Components with less than 100 bugs are not very useful, so we ignore them.
        low_count_components = {
            component: count
            for component, count in Counter(classes.values()).items()
            if count < 100
        }
        bugs_to_remove = [
            bug_id
            for bug_id, component in classes.items()
            if component in low_count_components
        ]
        for bug_id in bugs_to_remove:
            del classes[bug_id]

        return classes, set(classes.values())
