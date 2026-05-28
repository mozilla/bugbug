# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from logging import INFO, basicConfig, getLogger

basicConfig(level=INFO)
logger = getLogger(__name__)


DEFAULT_OPENAI_MODEL = "gpt-5-2025-08-07"
DEFAULT_ANTHROPIC_MODEL = "claude-opus-4-6"
# Cheap, fast model used for the risk/complexity pre-screen, separate from the
# review model so the gate stays inexpensive.
DEFAULT_SCORING_MODEL = "claude-haiku-4-5-20251001"


def usage_from_messages(messages) -> dict[str, int]:
    """Aggregate LangChain ``usage_metadata`` across one or more AI messages.

    Returns a dict with input/output token totals and the cache-read /
    cache-creation breakdown, suitable for cost estimation. Messages without
    usage metadata (e.g. tool or human messages) are ignored.
    """
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    for message in messages:
        usage = getattr(message, "usage_metadata", None)
        if not usage:
            continue
        totals["input_tokens"] += usage.get("input_tokens", 0) or 0
        totals["output_tokens"] += usage.get("output_tokens", 0) or 0
        details = usage.get("input_token_details") or {}
        totals["cache_read_input_tokens"] += details.get("cache_read", 0) or 0
        totals["cache_creation_input_tokens"] += details.get("cache_creation", 0) or 0
    return totals


def get_tokenizer(model_name):
    import tiktoken

    try:
        return tiktoken.encoding_for_model(model_name)
    except KeyError:
        FALLBACK_ENCODING = "cl100k_base"
        logger.info(
            "Tokenizer couldn't be found for %s, falling back to %s",
            model_name,
            FALLBACK_ENCODING,
        )
        return tiktoken.get_encoding(FALLBACK_ENCODING)
