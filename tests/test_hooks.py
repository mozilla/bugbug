# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import json
import os
import shutil

import jsone
import jsonschema
import pytest

from infra.set_hook_version import set_hook

with open(os.path.join("VERSION")) as f:
    version = f.read().strip()

parameters = [
    (os.path.realpath("infra/taskcluster-hook-annotate.json"), {}),
    (os.path.realpath("infra/taskcluster-hook-pipeline-start.json"), {}),
    (os.path.realpath("infra/taskcluster-hook-check-models-start.json"), {}),
    (os.path.realpath("infra/taskcluster-hook-classify-patch.json"), {"DIFF_ID": 123}),
    (os.path.realpath("infra/taskcluster-hook-test-select.json"), {"DIFF_ID": 123}),
]

for infra_path in os.listdir("infra"):
    if not infra_path.startswith("taskcluster-hook-"):
        continue

    assert any(
        path == os.path.realpath(os.path.join("infra", infra_path))
        for path, payload in parameters
    ), f"{infra_path} not found"


@pytest.mark.parametrize("hook_file,payload", parameters)
def test_jsone_validates(tmp_path, hook_file, payload):
    tmp_hook_file = tmp_path / "hook.json"

    shutil.copyfile(hook_file, tmp_hook_file)

    set_hook(tmp_hook_file, version)

    with open(tmp_hook_file, "r") as f:
        hook_content = json.load(f)

    jsonschema.validate(instance=payload, schema=hook_content["triggerSchema"])

    jsone.render(hook_content, context={"payload": payload})
