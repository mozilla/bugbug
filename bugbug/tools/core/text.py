# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Guardrails for LLM-generated text that gets posted publicly.

LLMs occasionally get stuck in a degenerate next-token loop and emit the same
word or short phrase thousands of times (see
https://github.com/mozilla/bugbug/issues/5995). When such output is posted as a
review comment it produces a massive, useless comment. The helpers here detect
that pathological output so the caller can fail (and retry) instead of posting
garbage.
"""

import re

from bugbug.tools.core.exceptions import RunawayGenerationError

# Upper bound on the length (in characters) of any LLM-generated text we post.
# A genuine patch summary or review comment is comfortably within this; anything
# larger is almost certainly a runaway generation.
MAX_GENERATED_TEXT_LENGTH = 10_000

# If a short chunk of text repeats this many times back-to-back, we treat the
# output as a degenerate model loop.
MIN_REPETITIONS = 10

# Longest chunk we look for when detecting consecutive repetition. This covers
# both single repeated words ("prevent prevent ...") and short repeated phrases
# ("not converting not converting ..."). Keeping it small also bounds the cost
# of the backreference-based search.
_MAX_REPEATING_CHUNK_LENGTH = 60

# Matches a chunk of up to ``_MAX_REPEATING_CHUNK_LENGTH`` characters that
# repeats at least ``MIN_REPETITIONS`` times in a row.
_REPETITION_RE = re.compile(
    r"(.{1,%d}?)\1{%d,}" % (_MAX_REPEATING_CHUNK_LENGTH, MIN_REPETITIONS - 1),
    re.DOTALL,
)


def find_runaway_repetition(text: str) -> int | None:
    """Locate a degenerate repetition loop in the text.

    Args:
        text: The text to inspect.

    Returns:
        The character offset where the repetition begins, or ``None`` if no
        runaway repetition was found.
    """
    match = _REPETITION_RE.search(text)
    return match.start() if match else None


def check_runaway_generation(
    text: str, label: str = "text", max_length: int = MAX_GENERATED_TEXT_LENGTH
) -> None:
    """Fail fast when LLM-generated text looks like a runaway generation.

    Detects the two symptoms of a model stuck in a degenerate loop: an
    excessively long output, or a short word/phrase repeated many times in a
    row. Either one raises :class:`RunawayGenerationError` so the caller can
    fail the generation (and let it be retried) rather than posting garbage.

    Args:
        text: The raw text produced by the model.
        label: A human-readable name for the text, used in the error message.
        max_length: The maximum number of characters to allow.

    Raises:
        RunawayGenerationError: If the text is too long or excessively repetitive.
    """
    if not text:
        return

    # Check the length first so the repetition search below only ever runs on a
    # bounded string, regardless of how large the original generation was.
    if len(text) > max_length:
        raise RunawayGenerationError(
            f"Generated {label} is too long: {len(text)} characters "
            f"(limit is {max_length})."
        )

    offset = find_runaway_repetition(text)
    if offset is not None:
        raise RunawayGenerationError(
            f"Generated {label} contains excessive repetition "
            f"starting at offset {offset}."
        )
