"""Which Firefox Source Docs cover a triaged component, read from the checkout.

The mapping comes from the tree's `SPHINX_TREES` declarations, not from a list kept here.
A component's `doc_trees` only names where to search when its docs are not under its code.
"""

import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

from .teamconfig import Component

# Where the in-tree Sphinx trees are published.
SOURCE_DOCS_BASE_URL = "https://firefox-source-docs.mozilla.org/"


class DocRef(NamedTuple):
    """One Firefox Source Docs tree: where the agent reads it, where a comment links it."""

    # In-tree directory, checkout-relative, which the agent reads directly.
    tree: str
    # Published path under `SOURCE_DOCS_BASE_URL`, for links in the comment.
    path: str

    @property
    def url(self) -> str:
        return f"{SOURCE_DOCS_BASE_URL}{self.path}"


# `SPHINX_TREES["<key>"] = "<source dir>"` or `= ["<source dir>"]`, at column zero, so an
# entry inside an `if` block or a comment is skipped.
_REGISTRATION = re.compile(
    r"""^SPHINX_TREES\[\s*["']([^"']+)["']\s*\]\s*=\s*\[?\s*["']([^"']+)["']"""
)


def _published_path(moz_build_dir: str, key: str) -> str:
    """Where a `SPHINX_TREES` key publishes, as a path under `SOURCE_DOCS_BASE_URL`.

    The two key forms mean different things, and `tools/moztreedocs/docs/nested-docs.rst`
    is explicit that the key need not correspond to a path in the tree at all:

    - Absolute is the URL verbatim. `extensions/permissions/moz.build` registers
      ``/permissions``, published at `/permissions/`.
    - Relative is appended to the `moz.build`'s own directory.
      `browser/installer/windows/moz.build` registers ``installer``, published at
      `/browser/installer/windows/installer/`.
    """
    # A registration in the root `moz.build` has no directory to hang a relative key
    # off, so it lands at the top level either way.
    if key.startswith("/") or not moz_build_dir:
        return key.strip("/") + "/"
    return f"{moz_build_dir}/{key.strip('/')}/"


def registrations(repo: Path) -> tuple[DocRef, ...]:
    """Every `SPHINX_TREES` registration in ``repo``, as in-tree dir and published path.

    Uses one `git grep`, which is much faster than a directory walk. ``-z`` and
    ``--no-line-number`` fix the output format whatever the user's gitconfig says.

    Fails open with a warning, because a component with no doc links still triages.
    """
    try:
        out = subprocess.run(
            [
                "git",
                "grep",
                "--no-color",
                "-z",
                "--no-line-number",
                r"^SPHINX_TREES\[",
                "--",
                "*moz.build",
            ],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as e:
        print(
            f"[gecko_triage] cannot search {repo} for documentation "
            f"({type(e).__name__}: {e}); components will have no doc links",
            file=sys.stderr,
        )
        return ()

    # `git grep` exits 1 for "no matches"; above that, the checkout is missing or broken.
    if out.returncode > 1:
        print(
            f"[gecko_triage] cannot search {repo} for documentation "
            f"(git grep exited {out.returncode}: {out.stderr.strip()}); "
            f"components will have no doc links",
            file=sys.stderr,
        )
        return ()

    found: list[DocRef] = []
    unparsed: list[str] = []
    for line in out.stdout.splitlines():
        path, sep, text = line.partition("\0")
        if not sep:
            # `Binary file <path> matches`, not a match record.
            continue
        match = _REGISTRATION.match(text)
        if match is None:
            unparsed.append(f"{path}: {text.strip()}")
            continue
        key, source = match.groups()
        moz_build_dir = str(Path(path).parent)
        moz_build_dir = "" if moz_build_dir == "." else moz_build_dir
        tree = f"{moz_build_dir}/{source}".lstrip("/") if moz_build_dir else source
        found.append(
            DocRef(tree=tree.rstrip("/"), path=_published_path(moz_build_dir, key))
        )

    # One line for all of them, so the log stays readable.
    if unparsed:
        print(
            f"[gecko_triage] {len(unparsed)} SPHINX_TREES "
            f"declaration(s) not understood, contributing no doc links "
            f"({'; '.join(unparsed)})",
            file=sys.stderr,
        )

    return tuple(sorted(set(found)))


def docs_for(entry: Component, known: tuple[DocRef, ...]) -> tuple[DocRef, ...]:
    """The registrations whose source directory sits under one of ``entry.trees``.

    An entry that sets `doc_trees` is searched under that instead, because its docs are
    not under its code (for example, one directory's `moz.build` registers the docs of
    several components). The override **replaces** `trees` rather than adding to it.

    A `trees` entry may name a file rather than a directory (`dir/File.cpp`),
    and the trailing slash is what says which. `test_plan.py` enforces the convention.
    """
    prefixes = []
    for tree in entry.doc_trees or entry.trees:
        if not tree.endswith("/"):
            tree = str(Path(tree).parent)
        prefixes.append(tree.rstrip("/") + "/")

    return tuple(
        ref for ref in known if any(f"{ref.tree}/".startswith(p) for p in prefixes)
    )
