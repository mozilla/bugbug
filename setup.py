# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import os

from setuptools import find_packages
from setuptools import setup

here = os.path.dirname(__file__)


def load_requirements(filename):
    with open(os.path.join(here, filename)) as f:
        return f.read().strip().split('\n')


with open(os.path.join(here, 'VERSION')) as f:
    version = f.read().strip()

setup(
    name='bugbug',
    version=version,
    description='ML tools for Mozilla projects',
    author='Marco Castelluccio',
    author_email='mcastelluccio@mozilla.com',
    install_requires=load_requirements('requirements.txt'),
    packages=find_packages(exclude=['contrib', 'docs', 'tests']),
    include_package_data=True,
    license='MPL2',
)
