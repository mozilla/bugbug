"""Expand Searchfox permalink placeholders in recorded Bugzilla comments.

A permalink pins a file to a revision, so a source reference in a triage comment
keeps pointing at the code that was actually reasoned about even after the tree
moves on. Rather than have the agent write (and risk mangling) a 40-character
SHA, it writes a placeholder and this module substitutes the prefix:

    [browser/base/content/browser.js#412]({{searchfox.permalink}}/browser/base/content/browser.js#412)

Mirrors the ``{{actions.<ref>.url}}`` convention, but expands at *record* time
rather than apply time: unlike a prior action's result, the revision is known
before the agent runs, and expanding early means the comment awaiting review in
``summary.json`` shows the real URLs a human can click.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)

# Searchfox renamed the Firefox tree from "mozilla-central" to "firefox-main"
# when it moved to git; the old name only 301-redirects.
TREE = "firefox-main"

_BASE_URL = f"https://searchfox.org/{TREE}"
_USER_AGENT = "bugbug-hackbot-runtime"

PLACEHOLDER = "{{searchfox.permalink}}"
# Tolerate inner whitespace — the agent writes this by hand.
_PLACEHOLDER = r"\{\{\s*searchfox\.permalink\s*\}\}"
# The placeholder as the whole URL of a Markdown link, so an unresolvable target
# can be degraded by unwrapping the link rather than just dropping its href.
_LINKED_PLACEHOLDER_RE = re.compile(
    rf"\[(?P<label>[^\]\n]*)\]\(\s*{_PLACEHOLDER}(?P<target>[^)\s]*)\s*\)"
)
# The placeholder anywhere else, with whatever path the agent appended to it.
_BARE_PLACEHOLDER_RE = re.compile(rf"{_PLACEHOLDER}(?P<target>\S*)")

# Every Searchfox *file* page carries the permalink its own UI would produce, as
# the href of the "Create a revision-specific link" panel item — which is the
# revision the index was built from. Any stable file works as the probe; a
# top-level moz.build is about as permanent as the tree gets.
_PROBE_PATH = "toolkit/moz.build"
_PROBE_URL = f"{_BASE_URL}/source/{_PROBE_PATH}"
_PANEL_PERMALINK_RE = re.compile(
    rf'<a id="panel-permalink" href="/{TREE}/rev/(?P<rev>[0-9a-f]{{40}})/'
)


def permalink_prefix(rev: str | None) -> str:
    """The URL prefix :data:`PLACEHOLDER` expands to.

    With no revision this degrades to Searchfox's revision-agnostic ``/source/``
    prefix: still a working link to the right file, just following the tip
    instead of pinned. That beats leaving an unexpanded placeholder (or a bare
    path) in a comment a human is about to read.
    """
    return f"{_BASE_URL}/rev/{rev}" if rev else f"{_BASE_URL}/source"


async def resolve_index_revision(*, client=None, timeout: float = 10.0) -> str | None:
    """Return the git SHA Searchfox's index is pinned to, or ``None``.

    Reads the permalink Searchfox publishes on a file page, so the revision is
    by construction one Searchfox can serve.

    Note this is deliberately *not* the checked-out repo's base commit. Searchfox
    only serves ``/rev/<sha>`` for revisions it has indexed, and its index runs
    behind the tip of ``firefox.git`` by hours (83 commits / ~20h when measured);
    the checkout's HEAD therefore 500s for about a day after a run — exactly the
    window in which someone reads the triage comment.

    Never raises: a caller that cannot get a revision should fall back to a
    revision-agnostic link rather than fail its run. ``client`` accepts an
    httpx-like async client for testing; when omitted, ``httpx`` is used.
    """
    try:
        if client is None:
            import httpx

            async with httpx.AsyncClient(
                timeout=timeout, follow_redirects=True
            ) as owned:
                resp = await owned.get(_PROBE_URL, headers={"User-Agent": _USER_AGENT})
        else:
            resp = await client.get(_PROBE_URL, headers={"User-Agent": _USER_AGENT})
        if resp.status_code != 200:
            log.warning("searchfox %s returned HTTP %s", _PROBE_PATH, resp.status_code)
            return None
        match = _PANEL_PERMALINK_RE.search(resp.text)
    except Exception as e:
        log.warning("could not resolve the searchfox index revision: %s", e)
        return None

    if match is None:
        log.warning("no permalink found on the searchfox %s page", _PROBE_PATH)
        return None
    return match.group("rev")


def _join(prefix: str, target: str) -> str:
    if not target:
        return prefix.rstrip("/")
    return f"{prefix.rstrip('/')}/{target.lstrip('/')}"


def _target_path(target: str) -> str:
    """The repo-relative path in a placeholder target, minus any ``#`` anchor."""
    return target.lstrip("/").split("#", 1)[0]


def expand_placeholders(
    text: str, prefix: str, *, repo_root: Path | None = None
) -> str:
    """Replace every :data:`PLACEHOLDER` in ``text`` with ``prefix``.

    With ``repo_root``, a placeholder whose path does not exist in the checkout
    is *not* linked: a Markdown link is unwrapped to its backticked label, and a
    bare placeholder degrades to its backticked path. Models do occasionally
    name a file that isn't there, and a plausible-looking permalink to a
    nonexistent path is worse than plain text — it 404s for whoever clicks it.

    The check is a heuristic, not a proof: the checkout and the revision the
    links are pinned to are hours apart, so a file added in between exists
    locally but not on Searchfox (and one deleted in between, vice versa). It
    catches the common case, which is an invented path.
    """

    def _linkable(target: str) -> bool:
        if repo_root is None:
            return True
        path = _target_path(target)
        # Nothing to verify (bare placeholder, or anchor only) — let it expand.
        return not path or (repo_root / path).is_file()

    def _link(match: re.Match[str]) -> str:
        label, target = match.group("label"), match.group("target")
        if _linkable(target):
            return f"[{label}]({_join(prefix, target)})"
        log.warning(
            "not linking %r: no such file in the checkout (left as plain text)",
            _target_path(target),
        )
        return f"`{label}`"

    def _bare(match: re.Match[str]) -> str:
        target = match.group("target")
        if _linkable(target):
            return _join(prefix, target)
        log.warning(
            "not linking %r: no such file in the checkout (left as plain text)",
            _target_path(target),
        )
        return f"`{target.lstrip('/')}`"

    # Links first, so their placeholders are consumed before the bare pass.
    text = _LINKED_PLACEHOLDER_RE.sub(_link, text)
    return _BARE_PLACEHOLDER_RE.sub(_bare, text)


def permalink_hook(
    prefix: str, repo_root: Path | None = None
) -> Callable[[dict], None]:
    """An action hook that expands permalink placeholders in a comment's text.

    Register for ``bugzilla.add_comment``; the recorded comment then carries real
    URLs instead of placeholders. ``repo_root`` is the agent's source checkout,
    used to skip linking paths that do not exist (see
    :func:`expand_placeholders`).
    """

    def hook(action: dict) -> None:
        params = action.get("params")
        if not isinstance(params, dict):
            return
        text = params.get("text")
        if isinstance(text, str):
            params["text"] = expand_placeholders(text, prefix, repo_root=repo_root)

    return hook
