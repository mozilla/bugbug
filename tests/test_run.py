# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import os
import subprocess
import sys

RUNPY = os.path.join(os.path.dirname(os.path.dirname(__file__)), "run.py")


def test_run():
    # Test running the training for the bug model.
    print([sys.executable, RUNPY, "--train", "--goal", "defect"])
    subprocess.run([sys.executable, RUNPY, "--train", "--goal", "defect"], check=True)

    # Test loading the trained model.
    subprocess.run([sys.executable, RUNPY, "--goal", "defect"], check=True)
