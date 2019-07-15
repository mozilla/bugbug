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

# Read the extra requirements
extras = ["nlp", "nn"]

extras_require = {}

for extra in extras:
    extras_require[extra] = read_requirements("extra-%s-requirements.txt" % extra)


setup(
    name="bugbug",
    version=version,
    description="ML tools for Mozilla projects",
    author="Marco Castelluccio",
    author_email="mcastelluccio@mozilla.com",
    install_requires=install_requires,
    extras_require=extras_require,
    packages=find_packages(exclude=["contrib", "docs", "tests"]),
    include_package_data=True,
    license="MPL2",
    entry_points={
        "console_scripts": [
            "bugbug-data-commits = scripts.commit_retriever:main",
            "bugbug-data-bugzilla = scripts.bug_retriever:main",
            "bugbug-train = scripts.trainer:main",
            "bugbug-check = scripts.check:main",
            "bugbug-microannotate-generate = scripts.microannotate_generator:main",
            "bugbug-classify-commit = scripts.commit_classifier:main",
        ]
    },
    classifiers=[
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3 :: Only",
        "License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)",
    ],
)
