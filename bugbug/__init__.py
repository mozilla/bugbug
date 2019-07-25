# -*- coding: utf-8 -*-

import logging

import pkg_resources

logging.basicConfig(level=logging.INFO)


def get_bugbug_version():
    return pkg_resources.get_distribution("bugbug").version
