# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from logging import INFO, basicConfig, getLogger

basicConfig(level=INFO)
logger = getLogger(__name__)


DEFAULT_OPENAI_MODEL = "gpt-5-2025-08-07"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-5-20250929"


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
