"""Tests for Searchfox permalink placeholder expansion and revision lookup."""

from hackbot_runtime.searchfox import (
    PLACEHOLDER,
    expand_placeholders,
    permalink_hook,
    permalink_prefix,
    resolve_index_revision,
)

REV = "d5c0bb96ad84524b445ee72323a4c91176d20b4c"
PATH = "browser/components/tabbrowser/content/tabgroup.js"
PINNED = f"https://searchfox.org/firefox-main/rev/{REV}"
TIP = "https://searchfox.org/firefox-main/source"


def _page(rev):
    """A cut-down Searchfox file page carrying its permalink panel item."""
    return (
        '<a id="panel-permalink" '
        f'href="/firefox-main/rev/{rev}/toolkit/moz.build" '
        'title="Create a revision-specific link of the current file"></a>\n'
        '<a id="panel-remove-permalink" '
        'href="/firefox-main/source/toolkit/moz.build"></a>'
    )


class FakeResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code


class FakeClient:
    """Minimal httpx-like async client; records the URL it was asked for."""

    def __init__(self, response):
        self._response = response
        self.urls: list[str] = []

    async def get(self, url, headers=None):
        self.urls.append(url)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def test_permalink_prefix_pins_a_revision():
    assert permalink_prefix(REV) == PINNED


def test_permalink_prefix_falls_back_to_revision_agnostic_source():
    # Without a revision the link must still work, just unpinned — better than
    # leaving an unexpanded placeholder in a comment.
    assert permalink_prefix(None) == TIP
    assert permalink_prefix("") == TIP


async def test_resolve_index_revision_reads_the_permalink_panel():
    client = FakeClient(FakeResponse(_page(REV)))
    assert await resolve_index_revision(client=client) == REV
    assert client.urls == [
        "https://searchfox.org/firefox-main/source/toolkit/moz.build"
    ]


async def test_resolve_index_revision_returns_none_on_failures():
    cases = [
        FakeClient(FakeResponse("", status_code=500)),
        FakeClient(FakeResponse("<html>no permalink here</html>")),
        # A short/abbreviated hash is not a usable permalink revision.
        FakeClient(FakeResponse(_page("d5c0bb9"))),
        FakeClient(RuntimeError("connection reset")),
    ]
    for client in cases:
        assert await resolve_index_revision(client=client) is None


def test_expand_placeholders_builds_the_url():
    text = f"Cause is in [{PATH}#412]({PLACEHOLDER}/{PATH}#412)."
    assert expand_placeholders(text, PINNED) == (
        f"Cause is in [{PATH}#412]({PINNED}/{PATH}#412)."
    )


def test_expand_placeholders_handles_every_occurrence_and_inner_spaces():
    text = f"{PLACEHOLDER}/a.js and {{{{ searchfox.permalink }}}}/b.js"
    assert expand_placeholders(text, PINNED) == f"{PINNED}/a.js and {PINNED}/b.js"


def test_expand_placeholders_leaves_other_text_alone():
    for text in [
        "No placeholder here.",
        "A different one: {{actions.patch.url}}",
        f"Bare path {PATH} stays bare.",
    ]:
        assert expand_placeholders(text, PINNED) == text


def test_expand_placeholders_does_not_treat_prefix_as_a_backreference():
    # A replacement containing \1 or \g would explode if passed to re.sub as a
    # template rather than a function.
    assert (
        expand_placeholders(PLACEHOLDER, r"https://x/\1\g<0>") == r"https://x/\1\g<0>"
    )


def _checkout(tmp_path, *paths):
    """A fake checkout containing exactly ``paths``."""
    for path in paths:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("// code")
    return tmp_path


def test_repo_root_unwraps_the_link_for_a_nonexistent_path(tmp_path):
    root = _checkout(tmp_path, PATH)
    missing = "browser/components/tabbrowser/content/invented.js"
    text = (
        f"Real: [{PATH}#9]({PLACEHOLDER}/{PATH}#9) "
        f"and invented: [{missing}]({PLACEHOLDER}/{missing})."
    )
    assert expand_placeholders(text, PINNED, repo_root=root) == (
        f"Real: [{PATH}#9]({PINNED}/{PATH}#9) and invented: `{missing}`."
    )


def test_repo_root_degrades_a_bare_placeholder_to_a_backticked_path(tmp_path):
    root = _checkout(tmp_path)
    missing = "browser/base/content/nope.js"
    assert expand_placeholders(
        f"See {PLACEHOLDER}/{missing}", PINNED, repo_root=root
    ) == (f"See `{missing}`")


def test_repo_root_keeps_the_label_when_it_is_not_the_path(tmp_path):
    # The agent may shorten the link text; unwrapping must preserve whatever it
    # wrote rather than substituting the path.
    root = _checkout(tmp_path)
    text = f"in [tabgroup.js]({PLACEHOLDER}/browser/components/nope/tabgroup.js)"
    assert expand_placeholders(text, PINNED, repo_root=root) == "in `tabgroup.js`"


def test_repo_root_still_expands_when_there_is_no_path_to_check(tmp_path):
    root = _checkout(tmp_path)
    assert expand_placeholders(PLACEHOLDER, PINNED, repo_root=root) == PINNED


def test_repo_root_does_not_link_a_directory(tmp_path):
    # is_file(), not exists(): a directory is not a source reference.
    root = _checkout(tmp_path, "browser/base/content/browser.js")
    text = f"[browser/base]({PLACEHOLDER}/browser/base)"
    assert expand_placeholders(text, PINNED, repo_root=root) == "`browser/base`"


def test_without_repo_root_every_path_is_linked(tmp_path):
    missing = "browser/components/nope.js"
    text = f"[{missing}]({PLACEHOLDER}/{missing})"
    assert expand_placeholders(text, PINNED) == f"[{missing}]({PINNED}/{missing})"


def test_permalink_hook_applies_the_existence_check(tmp_path):
    root = _checkout(tmp_path, PATH)
    missing = "browser/base/content/nope.js"
    action = {
        "type": "bugzilla.add_comment",
        "params": {
            "bug_id": 1,
            "text": f"[a]({PLACEHOLDER}/{PATH}) [b]({PLACEHOLDER}/{missing})",
        },
    }
    permalink_hook(PINNED, root)(action)
    assert action["params"]["text"] == f"[a]({PINNED}/{PATH}) `b`"


def test_permalink_hook_expands_a_comment_in_place():
    action = {
        "type": "bugzilla.add_comment",
        "params": {"bug_id": 1, "text": f"See {PLACEHOLDER}/{PATH}#9"},
    }
    permalink_hook(PINNED)(action)
    assert action["params"]["text"] == f"See {PINNED}/{PATH}#9"


def test_permalink_hook_tolerates_a_missing_or_odd_payload():
    # The hook must never be the reason an action fails to record.
    for action in [
        {"type": "bugzilla.add_comment"},
        {"type": "bugzilla.add_comment", "params": None},
        {"type": "bugzilla.add_comment", "params": {}},
        {"type": "bugzilla.add_comment", "params": {"text": None}},
    ]:
        permalink_hook(PINNED)(action)
