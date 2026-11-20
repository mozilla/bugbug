# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""The otari gateway the open-model harnesses reach their models through.

``OTARI_BASE_URL`` is the gateway's API base (``.../api``) and ``OTARI_API_KEY``
a key with the ``usage:read`` scope. Models are named as otari names them, e.g.
``deepinfra:Qwen/Qwen3.8-27B``. Every request of a run carries one tag, so the
run's usage and cost, subagents' included, come from otari.
"""

import asyncio
import datetime
import math
import os
import sys
import uuid
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from . import Run, add_usage, record_usage

PROVIDER = "otari"
TAG = "hackbot_attempt"
API_KEY_ENV = "OTARI_API_KEY"
# otari writes usage in batches, flushed about every second.
USAGE_FLUSH_S = 3


@dataclass
class Model:
    id: str
    context_window: int | None
    max_output_tokens: int | None
    reasoning: bool
    # otari's ``pricing``: USD per million tokens, e.g. ``input_price_per_million``.
    pricing: dict | None = None

    def cost(self, usage: dict[str, int]) -> float | None:
        """What ``usage`` costs at the model's prices; a cache rate it lacks is input's."""
        rates = self.pricing or {}
        if (
            rates.get("unit", "token") != "token"
            or "input_price_per_million" not in rates
        ):
            return None
        price_in = rates["input_price_per_million"]
        return (
            usage.get("input_tokens", 0) * price_in
            + usage.get("output_tokens", 0) * rates.get("output_price_per_million", 0)
            + usage.get("cache_read_input_tokens", 0)
            * rates.get("cache_read_price_per_million", price_in)
            + usage.get("cache_creation_input_tokens", 0)
            * rates.get("cache_write_price_per_million", price_in)
        ) / 1_000_000


def api_url() -> str:
    """The OpenAI-compatible root, which clients extend with ``/chat/completions``."""
    base = os.environ.get("OTARI_BASE_URL") or "https://api.otari.ai/api"
    return base.rstrip("/") + "/v1"


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {os.environ[API_KEY_ENV]}"}


async def model(name: str) -> Model:
    """The model's limits as otari serves it; fails for a model it can't serve."""
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"{api_url()}/models/{quote(name, safe='/:')}", headers=_headers()
        )
    response.raise_for_status()
    info = response.json()
    parameters = info.get("supported_parameters") or []
    return Model(
        id=name,
        context_window=info.get("context_window"),
        max_output_tokens=info.get("max_output_tokens"),
        reasoning=any("reasoning" in p for p in parameters),
        pricing=info.get("pricing"),
    )


async def register_price(model: Model) -> None:
    """Give the Weave project the model's otari prices, so it can cost its spans.

    Weave prices spans by model name, and does not know otari's. A model's first
    price covers spans already traced too; a changed price applies from now.
    """
    try:
        await asyncio.to_thread(_register_price, model)
    except Exception as exc:
        sys.stderr.write(f"could not register {model.id}'s price in Weave: {exc}\n")


def _register_price(model: Model) -> None:
    import weave

    rates = model.pricing or {}
    client = weave.get_client()
    if (
        client is None
        or rates.get("unit", "token") != "token"
        or "input_price_per_million" not in rates
    ):
        return
    price_in = rates["input_price_per_million"]
    costs = {
        "prompt_token_cost": price_in / 1e6,
        "completion_token_cost": rates.get("output_price_per_million", 0) / 1e6,
        "cache_read_input_token_cost": rates.get(
            "cache_read_price_per_million", price_in
        )
        / 1e6,
        "cache_creation_input_token_cost": rates.get(
            "cache_write_price_per_million", price_in
        )
        / 1e6,
    }
    known = client.query_costs(llm_ids=[model.id])
    if known:
        latest = max(known, key=lambda c: c.effective_date)
        if all(
            math.isclose(getattr(latest, k) or 0, v, rel_tol=1e-6)
            for k, v in costs.items()
        ):
            return
        since = datetime.datetime.now(datetime.UTC)
    else:
        since = datetime.datetime(2000, 1, 1, tzinfo=datetime.UTC)
    client.add_cost(
        llm_id=model.id, provider_id=PROVIDER, effective_date=since, **costs
    )


def new_tag() -> str:
    """An ``Otari-Tags`` header value naming one run."""
    return f"{TAG}={uuid.uuid4()}"


def usage_of(
    summary: dict, name: str
) -> tuple[dict[str, dict[str, int]], float | None]:
    """A usage summary's tokens, under ``name``, and its cost unless partly unpriced."""
    totals = summary.get("totals") or {}
    if not totals.get("request_count"):
        return {}, None
    cache_read = totals.get("cache_read_tokens", 0)
    cache_write = totals.get("cache_write_tokens", 0)
    usage = {
        "input_tokens": totals.get("billed_input_tokens", 0) - cache_read - cache_write,
        "output_tokens": totals.get("billed_output_tokens", 0),
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_write,
    }
    cost = None if totals.get("unpriced_requests") else totals.get("cost")
    return {name: usage}, cost


async def usage(tag: str, name: str) -> tuple[dict[str, dict[str, int]], float | None]:
    """What the run tagged ``tag`` used and cost, by otari's own accounting."""
    await asyncio.sleep(USAGE_FLUSH_S)
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"{api_url()}/key/usage/summary",
            params={"tag": tag.replace("=", ":", 1), "dimensions": "none"},
            headers=_headers(),
        )
    response.raise_for_status()
    return usage_of(response.json(), name)


async def settle(run: Run, tag: str, model: Model, turn) -> None:
    """Take the run's usage and cost from otari, which saw every request.

    The harnesses' streams leave out subagents, but otari can miss what it has not
    written yet, so each count is the larger of the two. What the stream left out
    is traced on the turn. The cost is otari's, unless the tokens priced at the
    model's rates come to more, as when otari has not written or priced them all,
    or its usage cannot be read.
    """
    try:
        stored, cost = await usage(tag, model.id)
    except Exception as exc:
        sys.stderr.write(f"otari usage unavailable: {exc}\n")
        stored, cost = {}, None
    for model_name, counts in stored.items():
        streamed = run.usage.get(model_name, {})
        rest = {k: max(0, v - streamed.get(k, 0)) for k, v in counts.items()}
        if any(rest.values()):
            record_usage(turn, rest, model_name, PROVIDER)
            add_usage(run, model_name, rest)
    tokens = run.usage.get(model.id)
    costs = [c for c in (cost, model.cost(tokens) if tokens else None) if c is not None]
    run.cost_usd = max(costs) if costs else None
