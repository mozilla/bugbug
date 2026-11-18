# Copyright 2026 Mozilla Foundation
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Which paths a candidate may change."""

from __future__ import annotations

import re

# Paths that define how performance tests are scheduled, run and reported.
PROTECTED_PATHS = (
    "testing/raptor/",
    "testing/talos/",
    "testing/mozharness/",
    "testing/performance/",
    "python/mozperftest/",
    "tools/tryselect/",
    "taskcluster/",
)
FORBIDDEN_PATHS = ("third_party/webkit/PerformanceTests/",)
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_ESCAPES = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13}


def forbidden(path: str, *, harness_changes_allowed: bool) -> bool:
    return (
        path == "try_task_config.json"
        or path.startswith(FORBIDDEN_PATHS)
        or (
            not harness_changes_allowed
            and (path == ".taskcluster.yml" or path.startswith(PROTECTED_PATHS))
        )
    )


def _unquote(value: str) -> str:
    """Undo git's C-style path quoting (core.quotePath)."""
    if not (len(value) >= 2 and value[0] == value[-1] == '"'):
        return value
    raw = value[1:-1].encode("latin-1", "backslashreplace")
    out = bytearray()
    i = 0
    while i < len(raw):
        if raw[i] == 0x5C and i + 1 < len(raw):  # backslash
            nxt = chr(raw[i + 1])
            if nxt in "01234567" and i + 3 < len(raw):
                out.append(int(raw[i + 1 : i + 4], 8))
                i += 4
                continue
            out.append(_ESCAPES.get(nxt, raw[i + 1]))
            i += 2
            continue
        out.append(raw[i])
        i += 1
    return out.decode("utf-8", "replace")


def _strip_prefix(value: str, prefix: str) -> str:
    value = _unquote(value.split("\t", 1)[0])
    if value == "/dev/null":
        return ""
    if not value.startswith(prefix):
        raise ValueError(f"Unexpected path in patch header: {value!r}")
    return value[len(prefix) :]


def _header_paths(rest: str) -> list[str]:
    """Both paths of a ``diff --git`` header, including quoted or spaced ones."""
    if rest.startswith('"'):
        end = rest.index('"', 1)
        while rest[end - 1] == "\\":
            end = rest.index('"', end + 1)
        first, second = rest[: end + 1], rest[end + 2 :]
        return [_strip_prefix(first, "a/"), _strip_prefix(second, "b/")]
    if rest.endswith('"'):
        start = rest.rindex(' "')
        return [
            _strip_prefix(rest[:start], "a/"),
            _strip_prefix(rest[start + 1 :], "b/"),
        ]
    if rest.startswith("a/"):
        # Unquoted paths may contain spaces. A single " b/" splits them; with
        # more, only an unrenamed file (equal halves) is unambiguous.
        if rest.count(" b/") == 1:
            first, second = rest.split(" b/")
            return [first[2:], second]
        half = (len(rest) - 1) // 2
        first, second = rest[:half], rest[half + 1 :]
        if rest[half] == " " and first[2:] == second[2:] and second.startswith("b/"):
            return [first[2:], second[2:]]
    raise ValueError(f"Cannot parse patch header: diff --git {rest}")


def patch_paths(patch: bytes) -> set[str]:
    """Every path a git patch touches. Raises ValueError for unparsable headers.

    Reads ``diff --git`` headers plus ``---``/``+++``, rename and copy lines, so
    quoted (non-ASCII), spaced and header-less hunks cannot hide a path. Hunk
    lengths are tracked so changed lines that look like headers are skipped.
    """
    lines = patch.decode("utf-8", "replace").splitlines()
    paths: set[str] = set()
    old = new = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        if old > 0 or new > 0:
            if line.startswith(" "):
                old, new = old - 1, new - 1
            elif line.startswith("-"):
                old -= 1
            elif line.startswith("+"):
                new -= 1
            elif line == "":
                old, new = old - 1, new - 1  # Blank context line, space stripped.
            elif not line.startswith("\\"):
                raise ValueError("Patch hunk is shorter than its header says")
        elif hunk := _HUNK.match(line):
            old = int(hunk.group(2) or 1)
            new = int(hunk.group(4) or 1)
        elif line.startswith("diff --git "):
            paths.update(_header_paths(line[len("diff --git ") :]))
        elif (
            line.startswith("--- ")
            and i + 2 < len(lines)
            and lines[i + 1].startswith("+++ ")
            and lines[i + 2].startswith("@@ ")
        ):
            paths.add(_strip_prefix(line[4:], "a/"))
            paths.add(_strip_prefix(lines[i + 1][4:], "b/"))
            i += 1
        elif line.startswith(("rename from ", "rename to ", "copy from ", "copy to ")):
            paths.add(_unquote(line.split(" ", 2)[2]))
        i += 1
    if old > 0 or new > 0:
        raise ValueError("Patch ends inside a hunk")
    paths.discard("")
    return paths
