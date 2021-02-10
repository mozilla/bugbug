# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from typing import Callable, Dict

import orjson
import zstandard

from bugbug_http import models


def test_get_config_specific_groups(
    mock_get_config_specific_groups: Callable[
        [Dict[str, float], Dict[str, float]], None
    ],
) -> None:
    assert models.get_config_specific_groups("test-linux1804-64/opt-*") == "OK"

    # Assert the test selection result is stored in Redis.
    result = orjson.loads(
        zstandard.ZstdDecompressor().decompress(
            models.redis.get(
                "bugbug:job_result:get_config_specific_groups:test-linux1804-64/opt-*"
            )
        )
    )
    assert result == [{"name": "test-group1"}]
