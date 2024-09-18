# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import os

from setuptools import find_packages, setup

here = os.path.dirname(__file__)


def read_requirements(file_):
    with open(os.path.join(here, file_)) as f:
        return sorted(list(set(line.split("#")[0].strip() for line in f)))


install_requires = read_requirements("requirements.txt")

with open(os.path.join(here, "VERSION")) as f:
    version = f.read().strip()

setup(
    name="bugbug-http-service",
    version=version,
    description="ML tools for Mozilla projects",
    author="Marco Castelluccio",
    author_email="mcastelluccio@mozilla.com",
    install_requires=install_requires,
    packages=find_packages(),
    include_package_data=True,
    license="MPL2",
    entry_points={
        "console_scripts": [
            "bugbug-http-worker = bugbug_http.worker:main",
            "bugbug-http-pulse-listener = bugbug_http.listener:main",
        ]
    },
    classifiers=[
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3 :: Only",
        "License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)",
    ],
)
