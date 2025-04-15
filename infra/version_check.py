# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import subprocess

with open("VERSION", "r") as f:
    version = f.read().rstrip()

try:
    p = subprocess.run(
        ["git", "describe", "--abbrev=0", "--tags"], check=True, capture_output=True
    )
except subprocess.CalledProcessError as e:
    print(f"{e.cmd} failed with return code {e.returncode}")
    print("stdout:")
    print(e.stdout)
    print("stderr:")
    print(e.stderr)
    raise RuntimeError("Failure while getting latest tag")

cur_tag = p.stdout.decode("utf-8")[1:].rstrip()

assert version == cur_tag, (
    f"Version in the VERSION file ({version}) should be the same as the current tag ({cur_tag})"
)
