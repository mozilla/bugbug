# -*- coding: utf-8 -*-

import logging

import pkg_resources

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s:%(levelname)s:%(name)s:%(message)s"
)


def get_bugbug_version():
    return pkg_resources.get_distribution("bugbug").version
