# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Dataset rows: a ``prompt``, and the ``tests`` that grade it.

``tests`` is a list of ``{"harness", "paths", "prefs"}`` suites run before and
after the agent's patch. Other fields are kept but unused.
"""

import json
from pathlib import Path


def load(ref: str) -> list[dict]:
    """Rows of a Weave dataset by ref, or of a local directory of ``<id>.json``."""
    path = Path(ref)
    if path.is_dir():
        return [
            {"id": p.stem, **json.loads(p.read_text())}
            for p in sorted(path.glob("*.json"))
        ]
    import weave

    # The rows themselves, not copies: Weave logs each one as a reference to its row.
    return list(weave.ref(ref).get().rows)
