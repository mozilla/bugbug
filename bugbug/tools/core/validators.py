# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Reusable pydantic helpers for tolerating malformed LLM tool arguments."""

from pydantic import BeforeValidator


def strip_enum_quotes(value):
    """Strip surrounding quotes/whitespace models sometimes add to enum args.

    LLMs occasionally double-encode string arguments, sending e.g. the literal
    '"exclude"' (quotes included) instead of 'exclude', which then fails enum
    validation. See https://github.com/mozilla/bugbug/issues/6140.
    """
    if isinstance(value, str):
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1].strip()
    return value


# Reusable ``BeforeValidator`` for ``Literal`` tool parameters fed by an LLM.
#
# Use as ``Annotated[Literal[...], StripEnumQuotes]`` so the value is unwrapped
# before validation. The generated JSON schema is unchanged (still an
# ``enum``), so strict-mode validation still rejects genuinely-invalid values
# at the API layer; only the parsing of the supplied value is relaxed.
StripEnumQuotes = BeforeValidator(strip_enum_quotes)
