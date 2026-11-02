# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from typing import Annotated, Literal, Optional

import pytest
from pydantic import BaseModel, ValidationError

from bugbug.tools.core.validators import StripEnumQuotes, strip_enum_quotes


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ('"exclude"', "exclude"),
        ("'only'", "only"),
        ('  "exclude" ', "exclude"),
        ("exclude", "exclude"),
        ("only", "only"),
        ("", ""),
        ('"', '"'),  # lone quote: not a wrapping pair, left untouched
        (None, None),
        (42, 42),  # non-str passthrough
    ],
)
def test_strip_enum_quotes(value, expected):
    assert strip_enum_quotes(value) == expected


class _Model(BaseModel):
    value: Optional[Annotated[Literal["only", "exclude"], StripEnumQuotes]] = None


def test_strip_enum_quotes_validator_accepts_double_encoded_value():
    assert _Model(value='"exclude"').value == "exclude"
    assert _Model(value="'only'").value == "only"
    assert _Model(value="exclude").value == "exclude"
    assert _Model().value is None


def test_strip_enum_quotes_validator_still_rejects_invalid_value():
    with pytest.raises(ValidationError):
        _Model(value="nonsense")


def test_strip_enum_quotes_schema_still_emits_enum():
    """Strict-mode validation relies on the JSON schema keeping the enum."""
    schema = _Model.model_json_schema()
    enum = schema["properties"]["value"]["anyOf"][0]["enum"]
    assert enum == ["only", "exclude"]
