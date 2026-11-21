# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""The one Firefox tree a run works in, reset between examples.

The mozconfig and objdir survive resets, so every build after the first is
incremental.
"""

import hashlib
import subprocess
from pathlib import Path

OBJDIR = "obj-eval"
MOZCONFIG = f"""\
ac_add_options --enable-application=browser
ac_add_options --disable-debug
ac_add_options --enable-optimize
ac_add_options --disable-debug-symbols
mk_add_options MOZ_OBJDIR=@TOPSRCDIR@/{OBJDIR}
"""


def _git(tree: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(tree), *args], check=True, capture_output=True, text=True
    ).stdout


def head(tree: Path) -> str:
    return _git(tree, "rev-parse", "HEAD").strip()


def mozconfig(tree: Path) -> Path:
    path = tree / ".mozconfig"
    if not path.exists() or path.read_text() != MOZCONFIG:
        path.write_text(MOZCONFIG)
    return path


def reset(tree: Path, base: str) -> None:
    """Back to ``base``, keeping only the mozconfig and the objdir."""
    _git(tree, "checkout", "-q", "-f", "--detach", base)
    _git(tree, "clean", "-fdxq", "-e", "/.mozconfig", "-e", f"/{OBJDIR}/")


def diff(tree: Path, base: str) -> str:
    """Everything changed since ``base``, new files included."""
    _git(tree, "add", "-A")
    return _git(tree, "diff", "--staged", base)


def state(tree: Path, base: str) -> str:
    """What the tree would be built from: ``base`` and the changes on it."""
    return f"{base} {hashlib.sha256(diff(tree, base).encode()).hexdigest()}"


def stamp(tree: Path) -> Path:
    """Where the objdir records the :func:`state` it was last built from."""
    return tree / OBJDIR / "eval-built"


def built_base(tree: Path) -> str | None:
    """The commit the objdir was last built at, with or without changes on it."""
    path = stamp(tree)
    return path.read_text().split()[0] if path.exists() else None


def built(tree: Path, base: str) -> bool:
    """Whether the objdir holds a build of the tree as it is, and none since.

    A build the agent ran itself leaves no stamp but newer binaries.
    """
    path = stamp(tree)
    if not path.exists() or path.read_text() != state(tree, base):
        return False
    stamped = path.stat().st_mtime
    binaries = (tree / OBJDIR / "dist" / "bin").rglob("*")
    return all(p.stat().st_mtime <= stamped for p in binaries if p.is_file())


def restore(tree: Path, base: str, path: str) -> None:
    """Put ``path`` back as it was at ``base``."""
    _git(tree, "rm", "-rq", "--cached", "--ignore-unmatch", "--", path)
    _git(tree, "checkout", "-q", base, "--", path)
    _git(tree, "clean", "-fdq", "--", path)
