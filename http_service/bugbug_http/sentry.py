#!/usr/bin/env python
# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging

import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration

from bugbug import get_bugbug_version


def setup_sentry(dsn, integrations=[]):
    logging_integration = LoggingIntegration(
        # Default behaviour: INFO messages will be included as breadcrumbs
        level=logging.INFO,
        # Change default behaviour (ERROR messages events)
        event_level=logging.WARNING,
    )
    sentry_sdk.init(
        dsn=dsn,
        integrations=[logging_integration] + integrations,
        release=get_bugbug_version(),
    )
