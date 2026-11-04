# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Facts about a patch."""

import re

_FILE_RE = re.compile(r"^diff --git a/(\S+) b/", re.MULTILINE)


def changed_files(patch: str) -> list[str]:
    return _FILE_RE.findall(patch)


def touches(patch: str, root: str) -> bool:
    return any(path.startswith(root.rstrip("/") + "/") for path in changed_files(patch))


def lines_changed(patch: str) -> int:
    return sum(
        line[:1] in "+-" and not line.startswith(("+++", "---"))
        for line in patch.splitlines()
    )
