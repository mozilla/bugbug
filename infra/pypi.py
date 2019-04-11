# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import os
import subprocess

import requests

r = requests.get(f'{os.environ["TASKCLUSTER_PROXY_URL"]}/secrets/v1/secret/project/relman/bugbug/deploy')
r.raise_for_status()
data = r.json()

os.environ['TWINE_USERNAME'] = data['secret']['pypi']['username']
os.environ['TWINE_PASSWORD'] = data['secret']['pypi']['password']
subprocess.run(['twine', 'upload', 'dist/*'], check=True)