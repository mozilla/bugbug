# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Cost from recorded tokens, one table for every vendor.

Rates are USD per million tokens; add other vendors' models here. A model without
a rate gets no cost rather than a wrong one.
"""

from __future__ import annotations

PRICES = {
    "claude-fable-5-1": {"in": 10.0, "out": 50.0},
    "claude-opus-5-5": {"in": 4.0, "out": 20.0},
    "claude-sonnet-5-5": {"in": 2.0, "out": 10.0},
    "claude-haiku-5-5": {"in": 0.10, "out": 0.50},
    # OpenAI, standard tier up to 272K input tokens; no charge for cache writes.
    "gpt-6-astra": {"in": 10.0, "cache_read": 1.0, "out": 50.0},
    "gpt-6.1-sol": {"in": 2.0, "cache_read": 0.10, "out": 10.0},
    "gpt-6-sol": {"in": 2.0, "cache_read": 0.20, "out": 10.0},
    "gpt-6-luna": {"in": 0.10, "cache_read": 0.01, "out": 0.50},
    "gpt-5.6-sol": {"in": 4.0, "cache_read": 0.40, "out": 20.0},
    "gpt-5.6-terra": {"in": 2.0, "cache_read": 0.20, "out": 12.0},
    "gpt-5.6-luna": {"in": 0.20, "cache_read": 0.02, "out": 1.20},
    "gpt-5.5": {"in": 5.0, "cache_read": 0.50, "out": 30.0},
    "gpt-5.3-codex": {"in": 1.75, "cache_read": 0.175, "out": 14.0},
}


def cost(usage: dict[str, dict[str, int]]) -> float | None:
    total = 0.0
    for model, counts in usage.items():
        rate = PRICES.get(model)
        if rate is None:
            return None
        cache_read = rate.get("cache_read", rate["in"] * 0.1)
        cache_write = rate.get("cache_write", rate["in"] * 1.25)
        total += (
            counts.get("input_tokens", 0) * rate["in"]
            + counts.get("output_tokens", 0) * rate["out"]
            + counts.get("cache_read_input_tokens", 0) * cache_read
            + counts.get("cache_creation_input_tokens", 0) * cache_write
        ) / 1_000_000
    return round(total, 4)
