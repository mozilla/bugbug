"""Tests for deriving Firefox Source Docs URLs from a checkout.

The absolute-versus-relative `SPHINX_TREES` key rule is the whole risk here. Reading a
relative key as absolute produces a plausible-looking URL that 404s, and a Bugzilla
comment citing one is worse than a comment citing nothing, so both forms are pinned
against a fixture that needs no checkout.
"""

import os
import subprocess
from pathlib import Path

import pytest
from hackbot_agents.frontend_triage.config import ScopedComponent
from hackbot_agents.frontend_triage.docs import (
    SOURCE_DOCS_BASE_URL,
    DocRef,
    docs_for,
    registrations,
)


def _repo(tmp_path: Path, files: dict[str, str], **config: str) -> Path:
    """A git repo with the given `moz.build` contents, since `registrations` greps it.

    ``config`` sets git config keys, spelled with underscores for dots
    (``grep_lineNumber="true"`` for ``grep.lineNumber``), so a test can reproduce a
    developer's gitconfig.
    """
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    for key, value in config.items():
        git("config", key.replace("_", ".", 1), value)
    git("add", "-A")
    return tmp_path


def test_an_absolute_key_is_the_url_verbatim(tmp_path):
    # `extensions/permissions/moz.build` really does this: its docs publish at
    # `/permissions/`, nowhere near where they sit in the tree. Appending the key to the
    # moz.build's directory would give `/extensions/permissions/permissions/`.
    repo = _repo(
        tmp_path,
        {"extensions/permissions/moz.build": 'SPHINX_TREES["/permissions"] = "docs"\n'},
    )
    assert registrations(repo) == (
        DocRef(tree="extensions/permissions/docs", path="permissions/"),
    )


def test_a_relative_key_hangs_off_the_moz_build_directory(tmp_path):
    # `browser/installer/windows/moz.build` really does this. Treating the key as
    # absolute would give `/installer/`, which does not exist.
    repo = _repo(
        tmp_path,
        {"browser/installer/windows/moz.build": 'SPHINX_TREES["installer"] = "docs"\n'},
    )
    assert registrations(repo) == (
        DocRef(
            tree="browser/installer/windows/docs",
            path="browser/installer/windows/installer/",
        ),
    )


def test_the_source_directory_need_not_be_called_docs(tmp_path):
    # `toolkit/components/messaging-system/moz.build` publishes `schemas` as `docs`, so
    # the two halves of a registration are genuinely independent.
    repo = _repo(
        tmp_path,
        {
            "toolkit/components/messaging-system/moz.build": 'SPHINX_TREES["docs"] = "schemas"\n'
        },
    )
    assert registrations(repo) == (
        DocRef(
            tree="toolkit/components/messaging-system/schemas",
            path="toolkit/components/messaging-system/docs/",
        ),
    )


def test_an_indented_registration_is_not_one(tmp_path):
    # Anchored to column zero, so a declaration inside a conditional -- which may not
    # apply to the build the docs are generated from -- is not read as unconditional.
    repo = _repo(
        tmp_path,
        {"gfx/moz.build": 'if CONFIG["FOO"]:\n    SPHINX_TREES["/gfx"] = "docs"\n'},
    )
    assert registrations(repo) == ()


def test_a_missing_or_non_git_directory_yields_nothing(tmp_path):
    # Failing open: a component gets no doc links, which is the same outcome as a
    # component whose trees hold no docs, rather than taking down the run.
    assert registrations(tmp_path / "nope") == ()


def test_docs_are_matched_to_a_component_by_its_trees(tmp_path):
    known = (
        DocRef(
            tree="toolkit/components/ipprotection/docs", path="toolkit/ipprotection/"
        ),
        DocRef(
            tree="browser/installer/windows/docs",
            path="browser/installer/windows/installer/",
        ),
    )
    entry = ScopedComponent(
        "Firefox",
        "IP Protection",
        "#chan",
        trees=("browser/components/ipprotection/", "toolkit/components/ipprotection/"),
    )
    assert docs_for(entry, known) == (known[0],)


def test_a_tree_naming_a_single_file_matches_its_directory():
    # Site permissions lists three specific modules rather than `browser/modules/`,
    # because owning the whole directory would refuse half the desktop frontend. Docs
    # still have to resolve for it.
    known = (DocRef(tree="browser/modules/docs", path="browser/modules/docs/"),)
    entry = ScopedComponent(
        "Firefox",
        "Site Permissions",
        "#chan",
        trees=("browser/modules/SitePermissions.sys.mjs",),
    )
    assert docs_for(entry, known) == known


def test_a_component_whose_trees_hold_no_docs_gets_none():
    known = (DocRef(tree="gfx/docs", path="gfx/"),)
    entry = ScopedComponent(
        "Firefox", "Sharing", "#chan", trees=("browser/components/sharing/",)
    )
    assert docs_for(entry, known) == ()


def test_doc_trees_replaces_the_search_rather_than_widening_it():
    # The invariant behind Data Sanitization's entry, and the one a future reader is most
    # likely to "fix" into a union. Its docs are registered by the anti-tracking
    # `moz.build`, while its own `browser/base/content/sanitize*` files sit under
    # `browser/base/moz.build`'s tabbrowser tree -- so a union hands the prompt an
    # unrelated component's documentation alongside the right article, and a comment
    # citing that is worse than one citing nothing.
    known = (
        DocRef(
            tree="toolkit/components/antitracking/docs",
            path="toolkit/components/antitracking/anti-tracking/",
        ),
        DocRef(
            tree="browser/base/content/docs/tabbrowser", path="browser/base/tabbrowser/"
        ),
    )
    entry = ScopedComponent(
        "Toolkit",
        "Data Sanitization",
        "#chan",
        trees=("browser/base/content/sanitizeDialog.js",),
        doc_trees=("toolkit/components/antitracking/docs/",),
    )
    assert docs_for(entry, known) == (known[0],)

    # And without the override the same entry gets the wrong one, which is what the
    # override exists for. Asserting it keeps the test honest about the alternative.
    assert docs_for(entry._replace(doc_trees=()), known) == (known[1],)


def test_a_gitconfig_that_adds_line_numbers_does_not_break_parsing(tmp_path):
    # `grep.lineNumber = true` is an ordinary convenience setting, and it changes
    # `git grep` output to `path:lineno:content`. Splitting on the first colon then hands
    # the regex "1:SPHINX_TREES[...]", which does not match -- so every component
    # silently loses its docs while the prompt still tells the agent to go and read them.
    # Measured against the real checkout: 0 of 134 registrations parsed.
    repo = _repo(
        tmp_path,
        {"extensions/permissions/moz.build": 'SPHINX_TREES["/permissions"] = "docs"\n'},
        grep_lineNumber="true",
    )
    assert registrations(repo) == (
        DocRef(tree="extensions/permissions/docs", path="permissions/"),
    )


def test_a_checkout_with_no_declarations_is_quiet(tmp_path, capsys):
    # Exit code 1 means "no matches", which is a legitimate answer and not worth a word.
    repo = _repo(tmp_path, {"gfx/moz.build": "DIRS += []\n"})
    assert registrations(repo) == ()
    assert capsys.readouterr().err == ""


def test_a_broken_checkout_is_reported(tmp_path, capsys):
    # Exit code 128 means git could not answer at all. Failing open is right, but doing
    # it silently leaves an operator with a run whose docs vanished and no reason why.
    assert registrations(tmp_path / "not-a-repo") == ()
    err = capsys.readouterr().err
    assert "not-a-repo" in err


def test_the_list_form_of_a_declaration_is_parsed(tmp_path):
    # Real instance: toolkit/modules/subprocess/moz.build declares
    # SPHINX_TREES["toolkit_modules/subprocess"] = ["docs"]. `git grep` finds it and the
    # scalar-only regex dropped it, so the tree had 135 declarations and 134 parsed.
    #
    # The expected path doubles back on itself because the key is relative and happens to
    # restate its own directory. That really is where it publishes: I confirmed
    # /toolkit/modules/subprocess/toolkit_modules/subprocess/ loads and the absolute
    # reading, /toolkit_modules/subprocess/, is a 404.
    repo = _repo(
        tmp_path,
        {
            "toolkit/modules/subprocess/moz.build": 'SPHINX_TREES["toolkit_modules/subprocess"] = ["docs"]\n'
        },
    )
    assert registrations(repo) == (
        DocRef(
            tree="toolkit/modules/subprocess/docs",
            path="toolkit/modules/subprocess/toolkit_modules/subprocess/",
        ),
    )


def test_a_declaration_that_cannot_be_parsed_is_reported(tmp_path, capsys):
    # The failure that matters is not the unsupported spelling, it is dropping one
    # without saying so. A new `SPHINX_TREES` form should cost a log line, not a
    # component's documentation.
    repo = _repo(tmp_path, {"gfx/moz.build": "SPHINX_TREES[FOO] = BAR\n"})
    assert registrations(repo) == ()
    assert "gfx/moz.build" in capsys.readouterr().err


def test_a_dotted_directory_does_not_inherit_its_parents_docs(tmp_path):
    # The old heuristic read any basename containing a dot as a file and used its parent,
    # so `widget/foo.bar/` matched on `widget/` and picked up a sibling's documentation.
    # Supplying the wrong docs is worse than supplying none: it puts another component's
    # URLs and prose in the prompt.
    known = (DocRef(tree="widget/unrelated/docs", path="widget/unrelated/"),)
    entry = ScopedComponent("Firefox", "Dotted", "#chan", trees=("widget/foo.bar/",))
    assert docs_for(entry, known) == ()


_REPO = os.environ.get("SOURCE_REPO")


@pytest.mark.skipif(
    not (_REPO and Path(_REPO).is_dir()),
    reason="needs a mozilla-central checkout; set SOURCE_REPO",
)
def test_the_derivation_agrees_with_a_real_checkout():
    # Three spot checks against the real tree, all of which I confirmed load. The point is
    # that the rule is right, not that any particular component has docs -- a component
    # legitimately has none, and a stale checkout legitimately has neither.
    known = registrations(Path(_REPO))
    assert known, (
        "no SPHINX_TREES declarations found; is SOURCE_REPO a firefox checkout?"
    )

    urls = {
        d.url
        for entry in (
            ScopedComponent(
                "Firefox",
                "IP Protection",
                "#c",
                trees=("toolkit/components/ipprotection/",),
            ),
            ScopedComponent(
                "Firefox", "Installer", "#c", trees=("browser/installer/",)
            ),
            # The `doc_trees` case, and the reason the field exists: this article is
            # registered by `toolkit/components/antitracking/moz.build`, so it resolves
            # only because the entry names that directory rather than its own code.
            ScopedComponent(
                "Toolkit",
                "Data Sanitization",
                "#c",
                trees=("toolkit/components/cleardata/",),
                doc_trees=("toolkit/components/antitracking/docs/",),
            ),
        )
        for d in docs_for(entry, known)
    }
    assert f"{SOURCE_DOCS_BASE_URL}toolkit/ipprotection/" in urls
    assert f"{SOURCE_DOCS_BASE_URL}browser/installer/windows/installer/" in urls
    assert (
        f"{SOURCE_DOCS_BASE_URL}toolkit/components/antitracking/anti-tracking/" in urls
    )
