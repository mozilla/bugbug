"""Which Firefox Source Docs cover a triaged component, worked out from the checkout.

Nothing here is written down in `config.py`. mozilla-central already records the mapping
in `SPHINX_TREES` declarations, and restating it would be one more list to keep in step
with a tree we do not control -- the thing this module exists to stop.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

from .config import ScopedComponent

# Where mozilla-central's in-tree Sphinx trees are published. `docs.registrations` turns a
# `SPHINX_TREES` declaration into a path under this, so no component here carries a doc
# URL of its own -- that list would be one more thing to keep in step with a tree we do
# not control, which is the duplication this replaced.
SOURCE_DOCS_BASE_URL = "https://firefox-source-docs.mozilla.org/"


class DocRef(NamedTuple):
    """One Firefox Source Docs tree: where the agent reads it, where a comment links it."""

    # In-tree directory, checkout-relative. The agent has the whole tree, so it reads the
    # `.rst`/`.md` directly rather than fetching the rendered page: offline, no new
    # network capability, and it matches the revision it is reasoning about.
    tree: str
    # Published path under `SOURCE_DOCS_BASE_URL`, for the comment. A reader gets a link
    # they can click instead of a path they have to go and find.
    path: str

    @property
    def url(self) -> str:
        return f"{SOURCE_DOCS_BASE_URL}{self.path}"


# `SPHINX_TREES["<key>"] = "<source dir>"`, anchored so an entry inside an `if` block or a
# comment is skipped. Every real registration in the tree is written at column zero.
#
# The right-hand side has two spellings and both are live: a bare string, and a
# single-element list -- `toolkit/modules/subprocess/moz.build` uses
# `["docs"]`. Accepting only the first quietly cost that tree its documentation, which is
# why `registrations` now reports what it could not parse instead of skipping it.
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

    Reading a relative key as absolute yields `/installer/`, which is a plausible-looking
    URL that 404s. A comment citing one is worse than a comment citing none, so this is
    the part `tests/test_docs.py` pins hardest.
    """
    # A registration in the root `moz.build` has no directory to hang a relative key
    # off, so it lands at the top level either way.
    if key.startswith("/") or not moz_build_dir:
        return key.strip("/") + "/"
    return f"{moz_build_dir}/{key.strip('/')}/"


def registrations(repo: Path) -> tuple[DocRef, ...]:
    """Every `SPHINX_TREES` registration in ``repo``, as in-tree dir and published path.

    One `git grep` rather than a directory walk: ~135 registrations in under half a
    second, where walking `mobile/android/` alone costs more than that. The checkout is
    always a git clone (`hackbot.toml` clones `mozilla-firefox/firefox`), so there is no
    non-git fallback worth carrying.

    ``-z`` and ``--no-line-number`` are not belt-and-braces. Without them the output
    format is whatever the ambient gitconfig says: `grep.lineNumber = true` turns every
    line into `path:lineno:content`, and splitting on the first colon then hands the regex
    a line starting with a digit. Nothing matches, every component loses its docs, and the
    prompt still tells the agent to go and read them. ``-z`` additionally separates the
    path from the content with a NUL, so a path containing a colon parses correctly. A
    path containing a *newline* would still confuse the record split, which is left alone:
    mozilla-central has none, and one would break most of the tooling around it.

    Fails open, because a component with no doc links still triages, but says so: this
    used to return empty for a broken checkout and a documentation-free one alike, which
    left an operator with no way to tell a bug from a fact.
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
            f"[frontend_triage] cannot search {repo} for documentation "
            f"({type(e).__name__}: {e}); components will have no doc links",
            file=sys.stderr,
        )
        return ()

    # `git grep` exits 1 for "no matches", which is an answer, not a failure. Anything
    # above that is git being unable to answer at all -- a missing or corrupt checkout.
    if out.returncode > 1:
        print(
            f"[frontend_triage] cannot search {repo} for documentation "
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
            # Not a match record. `git grep` writes `Binary file <path> matches` for a
            # file containing a NUL, and reporting that as an unparsable declaration
            # would be a warning about nothing.
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

    # One line however many there are, and phrased as what is true: most declarations in
    # the tree are for directories no triaged component claims, so this is a heads-up that
    # a new `SPHINX_TREES` spelling exists, not a report of lost documentation. Per-line
    # warnings for a condition in a tree we do not control is how a log gets ignored.
    if unparsed:
        print(
            f"[frontend_triage] {len(unparsed)} SPHINX_TREES "
            f"declaration(s) not understood, contributing no doc links "
            f"({'; '.join(unparsed)})",
            file=sys.stderr,
        )

    return tuple(sorted(set(found)))


def docs_for(entry: ScopedComponent, known: tuple[DocRef, ...]) -> tuple[DocRef, ...]:
    """The registrations whose source directory sits under one of ``entry.trees``.

    A `trees` entry may name a file rather than a directory
    (`browser/modules/SitePermissions.sys.mjs`), and the trailing slash is what says
    which. That is a convention `test_plan.py` enforces rather than a guess: sniffing for
    a dot in the basename read `widget/foo.bar/` as a file and searched `widget/` instead,
    which handed the component a sibling's documentation. Wrong docs in the prompt are
    worse than none.

    Broad trees pick up siblings -- `mobile/android/` would match `focus-android/docs` as
    well as `fenix/docs` -- which is fixed by narrowing the entry's trees, not by
    filtering here: a filter would have to guess, and the trees are the component's own
    claim about where its code is.
    """
    prefixes = []
    for tree in entry.trees:
        if not tree.endswith("/"):
            tree = str(Path(tree).parent)
        prefixes.append(tree.rstrip("/") + "/")

    return tuple(
        ref for ref in known if any(f"{ref.tree}/".startswith(p) for p in prefixes)
    )
