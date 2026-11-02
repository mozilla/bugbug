# -*- coding: utf-8 -*-

import importlib.metadata
import logging

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s:%(levelname)s:%(name)s:%(message)s"
)


def get_bugbug_version():
    return importlib.metadata.version("bugbug")
