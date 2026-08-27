from typing import NamedTuple

# Bugzilla MCP tool names as exposed to the agent (mcp__<server>__<tool>).
BUGZILLA_READ_TOOLS = [
    "mcp__bugzilla__search_bugs",
    "mcp__bugzilla__get_bugs",
    "mcp__bugzilla__get_bug_comments",
    "mcp__bugzilla__get_bug_attachments",
    "mcp__bugzilla__download_attachment",
]


# Searchfox code-search tools (in-process MCP server "searchfox"). Symbol/def/
# text lookup + blame across mozilla-central — the agent's main code-navigation
# capability for localizing behavioral bugs.
SEARCHFOX_TOOLS = [
    "mcp__searchfox__search_identifier",
    "mcp__searchfox__search_text",
    "mcp__searchfox__find_definition",
    "mcp__searchfox__get_function_at_line",
    "mcp__searchfox__get_blame",
    "mcp__searchfox__get_file",
]

# Mozilla VCS / HGMO tools (in-process MCP server "mozilla_vcs"). Read a known
# regressor changeset's diff/metadata and recent file history over HTTP.
MOZILLA_VCS_TOOLS = [
    "mcp__mozilla_vcs__get_commit_info",
    "mcp__mozilla_vcs__get_commit_diff",
    "mcp__mozilla_vcs__file_history",
]


# Per-component triage guidance (in-process MCP server "guidance"). Only reached when the
# agent localizes outside the component the bug was filed in; the usual case is already in
# the prompt.
GUIDANCE_TOOLS = [
    "mcp__guidance__load_component_guidance",
]


# Recordable action types the agent may take, by dotted id. A comment is the only one:
# `bugzilla.update_bug` was here for `severity`, which is now a suggestion in the comment
# instead, leaving the tool with no caller.
#
# Dropping it also drops the `editbugs` requirement on the apply account, which mattered:
# the apply step coalesces a same-bug field change with the nearest comment into one PUT,
# so a rejected field change used to take the analysis comment down with it.
ENABLED_ACTION_TYPES = [
    "bugzilla.add_comment",
]


class ScopedComponent(NamedTuple):
    """A Bugzilla component sent here for triage, and where a finished run reports it."""

    product: str
    component: str
    # Required, because an entry without one would be a component getting unattended
    # triage with nobody told -- which is what `channel_for` failing closed produces,
    # and not something to be able to express by accident.
    channel: str
    # Where this component's code lives, for the prompt's index and for `docs.docs_for`.
    # Descriptive, so it may be broad and overlap another component.
    trees: tuple[str, ...]
    # Paths whose bugs belong to this component, for `owners_for_path` and so for
    # `hooks.component_guidance_hook`. Deliberately narrower than `trees`: a tree like
    # `browser/` would refuse comments the guidance itself asked for -- IP Protection
    # sends the agent to `browser/app/profile/firefox.js` for its prefs, which nothing
    # here may claim.
    #
    # Two components **may** declare the same entry, and the three Android ones do: that
    # reads as "either team's guidance is enough for this file". Declaring a nested path
    # is how one component takes a subtree out of a shared one, since the longer claim
    # wins outright.
    owns: tuple[str, ...] = ()
    # Triage guidance that no source doc carries: which of two similar things this bug is
    # about, what a symptom in one layer usually means about another, whether the area is
    # tested. Everything structural belongs in the docs `trees` resolves to, not here --
    # if a sentence restates a doc page, delete it rather than paraphrase it.
    notes: str = ""
    # Components sent alongside this one, for bugs that routinely turn out to be somewhere
    # else: a "stop sharing" report arrives under Sharing but is WebRTC, which site
    # permissions owns. Both ship from the start rather than the agent having to notice
    # mid-run.
    related: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        return f"{self.product} :: {self.component}"


# The components that are sent here for triage, and the channel that owns each. The
# single source of truth for routing: `SLACK_CHANNELS` below is derived from it, and
# `render_scope` in agent.py renders it into the system prompt, so adding a component is
# one entry here rather than the same name written into three prose lists and a test.
#
# This is narrower than what the agent will triage. `rules/scoping.md` puts any
# user-facing Firefox defect in scope, and a bug handed to the agent by hand is triaged
# on that rule whether or not its component is named here -- it just reports to nobody.
# What this tuple decides is routing, and it should stay in step with bugbot's
# `TRIAGED_COMPONENTS`, which decides what arrives automatically.
#
# A channel belongs to the team that owns the component, so the routing does too: a
# component that is not listed sends nothing, since posting one team's triage into
# another team's channel is worse than silence. There is deliberately no default channel.
#
# `slack.post_message` is left out of `ENABLED_ACTION_TYPES` on purpose. The message is
# code (see notify.py), not a model turn, so it goes through the recorder directly and
# the agent is never given the tool -- it has no say in what is said or where.
TRIAGE_SCOPE = (
    ScopedComponent(
        "Firefox",
        "New Tab Page",
        "#hnt-dev-triage",
        trees=("browser/extensions/newtab/", "browser/components/newtab/"),
        owns=("browser/extensions/newtab/", "browser/components/newtab/"),
        notes=(
            "Two directories, and the docs above are registered under the first. "
            "`browser/components/newtab/` is the about: module and startup-cache glue "
            "that serves about:home and about:newtab. The cache it maintains is "
            "documented in `browser/extensions/newtab/docs/v2-system-addon/"
            "about_home_startup_cache.md` -- filed under the first directory rather "
            "than beside the code it describes, which is why it is easy to miss. "
            "`browser/extensions/newtab/` is everything else: the React UI in "
            "`content-src/` **and** the feeds, prefs and message channel in `lib/`, "
            "which is `.sys.mjs` and comparable in size to the UI. So a page that fails "
            "to load at "
            "all is usually the glue, anything rendered wrong is `content-src/`, and a "
            'wrong story, ad or pref is `lib/` -- do not read "the frontend" as meaning '
            "only the React half."
        ),
    ),
    ScopedComponent(
        "Firefox",
        "Site Permissions",
        "#privacy-team-automation",
        trees=(
            "browser/modules/SitePermissions.sys.mjs",
            "browser/modules/PermissionUI.sys.mjs",
            "browser/actors/WebRTCParent.sys.mjs",
            "extensions/permissions/",
        ),
        owns=(
            "browser/modules/SitePermissions.sys.mjs",
            "browser/modules/PermissionUI.sys.mjs",
            "browser/actors/WebRTCParent.sys.mjs",
            "extensions/permissions/",
        ),
        notes=(
            "Split across the prompt, the state, and the store, so work out which of the "
            "three the bug is in before reading any of them. Two consequences the layering "
            "does not make obvious: camera, microphone and screen sharing go through "
            "`browser/actors/WebRTCParent.sys.mjs` and **not** the generic doorhanger "
            "path, so a prompt bug about those is not in `PermissionUI.sys.mjs`; and the "
            'backing store is C++ in `extensions/permissions/`, so "the permission did '
            'not stick", "it came back after a restart" and wrong-expiry bugs localize '
            "there and are **not** out of scope for being non-JS."
        ),
    ),
    ScopedComponent(
        "Firefox",
        "Sharing",
        "#content-sharing-automation",
        trees=("browser/components/sharing/",),
        # Not `widget/`: that is the whole platform widget layer, and a bug in any other
        # component citing a file there has nothing to do with sharing a URL out.
        owns=(
            "browser/components/sharing/",
            "widget/nsIMacSharingService.idl",
            "widget/cocoa/nsMacSharingService.mm",
            "widget/windows/nsSharePicker.cpp",
            "widget/windows/nsSharePicker.h",
        ),
        notes=(
            '**Two unrelated things are called "sharing" in this tree.** This '
            "component is sharing a URL _out_ to another app. Screen, camera and "
            'microphone sharing (the sharing indicator, the "stop sharing" button, '
            "per-tab sharing state) is WebRTC. It lives in "
            "`browser/actors/WebRTCParent.sys.mjs` and belongs to site permissions. A "
            'grep for `sharing` returns both, so a bug about an indicator or a "stop '
            'sharing" control is almost certainly the WebRTC one.\n\n'
            "**The platform half is in `widget/`**: "
            "`widget/nsIMacSharingService.idl` with "
            "`widget/cocoa/nsMacSharingService.mm` for macOS, and "
            "`widget/windows/nsSharePicker.cpp` for Windows. It is per-OS and **not** "
            "out of scope for being C++ or Objective-C++ rather than JS: "
            '"the Share menu is empty", '
            '"the wrong apps are listed" and "Share does nothing" usually localize '
            "there. Note which OS the report is about before reading either half, "
            "because their coverage differs and neither is simply untested. "
            "`widget/tests/unit/test_macsharingservice.js` is mac-only and drives the "
            "real service, asserting `getSharingProviders` returns usable providers, so "
            "check it first for an empty-menu or wrong-apps bug instead of reporting no "
            "coverage. What is genuinely unverified is `shareUrl` itself and the Windows "
            "platform code: the panel test mocks `nsIWindowsUIUtils`, so nothing opens "
            "the real share sheet or dialog."
        ),
        related=("Firefox :: Site Permissions",),
    ),
    ScopedComponent(
        "Firefox",
        "IP Protection",
        "#team-eng-ip-protection-triage",
        trees=("browser/components/ipprotection/", "toolkit/components/ipprotection/"),
        owns=("browser/components/ipprotection/", "toolkit/components/ipprotection/"),
        notes=(
            "**The canonical state is in the service, but the panel keeps its own**, "
            "so a symptom in the panel does not tell you which. `IPProtectionService` "
            "and `IPPProxyManager` hold entitlement and connection state; the panel "
            "derives UI state from them, usually via `setState` but at least once by "
            "mutating a `this.state` property directly (`hiding()`), so do not assume a "
            "panel bug must originate upstream.\n\n"
            "Two state machines both have a `READY`, which the docs above describe. "
            'What matters for triage is saying which one you mean: "it showed '
            'connected when it was not" and "it came back on after I turned it off" '
            'are proxy-connection bugs, while "the panel offered it to a user who is '
            'not entitled" is an entitlement bug.'
        ),
    ),
    ScopedComponent(
        "Firefox",
        "Messaging System",
        "#omc-triage",
        trees=(
            "browser/components/asrouter/",
            "browser/components/aboutwelcome/",
            "toolkit/components/messaging-system/",
        ),
        owns=(
            "browser/components/asrouter/",
            "browser/components/aboutwelcome/",
            "toolkit/components/messaging-system/",
        ),
        notes=(
            "**A message is data, not code.** Definitions come from providers that may "
            'be in-tree or remote, so "I saw the wrong message", "I '
            'saw it twice" and "I never saw it" are usually a definition, targeting or '
            "frequency-cap problem rather than a defect in the router. Say which of the "
            "two you think it is. If it is the message, look for an in-tree provider "
            "before concluding the definition is remote and unreadable: the local ones "
            "are the `.sys.mjs` modules registered in `LOCAL_MESSAGE_PROVIDERS`, "
            "`OnboardingMessageProvider` and `CFRMessageProvider`, **not** JSON -- the "
            "`.json` files alongside them are schemas. Feature-callout definitions arrive "
            "through the onboarding provider rather than registering their own. Say what "
            "would confirm it. A rendering or interaction bug in the surface itself is the "
            "ordinary case and localizes normally, but the router is shared by every "
            "surface, so work out which surface the reporter was on first."
        ),
    ),
    ScopedComponent(
        "Firefox for Android",
        "History",
        "#android-core-dev",
        trees=("mobile/android/fenix/", "mobile/android/android-components/"),
        # All three claim the shared trees so that a bug localized anywhere in
        # Fenix reaches this team, and the narrower entries below still win by
        # length. Before this was plural, most of `mobile/android/` was owned by
        # nobody and the citation hook stopped firing there.
        owns=(
            "mobile/android/fenix/",
            "mobile/android/android-components/",
            "mobile/android/fenix/app/src/main/java/org/mozilla/fenix/library/history/",
        ),
        notes=(
            "Fenix is mid-migration to Jetpack Compose, so a screen may have both a "
            "`…View.kt` and a Compose function and only one of them is live. Check which "
            "one the Fragment actually builds before planning against either: a fix "
            "planned against the retired implementation reads correct and changes nothing."
        ),
    ),
    ScopedComponent(
        "Firefox for Android",
        "Toolbar",
        "#android-core-dev",
        trees=("mobile/android/fenix/", "mobile/android/android-components/"),
        # All three claim the shared trees so that a bug localized anywhere in
        # Fenix reaches this team, and the narrower entries below still win by
        # length. Before this was plural, most of `mobile/android/` was owned by
        # nobody and the citation hook stopped firing there.
        owns=(
            "mobile/android/fenix/",
            "mobile/android/android-components/",
            "mobile/android/fenix/app/src/main/java/org/mozilla/fenix/components/toolbar/",
            "mobile/android/fenix/app/src/main/java/org/mozilla/fenix/home/toolbar/",
            "mobile/android/android-components/components/compose/browser-toolbar/",
            "mobile/android/android-components/components/browser/toolbar/",
        ),
        notes=(
            "**There are two toolbars, and two generations of the widget under each.** "
            "The browser toolbar is `…/fenix/components/toolbar/`; the homepage has its "
            "own at `…/fenix/home/toolbar/`. So work out which surface the reporter was "
            "on first: a `Homepage` bug can localize into a toolbar file and a "
            "`Toolbar` bug into the homepage. Underneath both, android-components has a "
            "newer Compose widget at "
            "`mobile/android/android-components/components/compose/browser-toolbar/` and "
            "the older View-based one at "
            "`mobile/android/android-components/components/browser/toolbar/`. Confirm "
            "which one Fenix builds before citing it."
        ),
    ),
    ScopedComponent(
        "Firefox for Android",
        "Homepage",
        "#android-core-dev",
        trees=("mobile/android/fenix/", "mobile/android/android-components/"),
        # All three claim the shared trees so that a bug localized anywhere in
        # Fenix reaches this team, and the narrower entries below still win by
        # length. Before this was plural, most of `mobile/android/` was owned by
        # nobody and the citation hook stopped firing there.
        owns=(
            "mobile/android/fenix/",
            "mobile/android/android-components/",
            "mobile/android/fenix/app/src/main/java/org/mozilla/fenix/home/",
        ),
        notes=(
            'One screen assembled from one package per section, so "which section" comes '
            'before "which file": a bug about the top-sites row or the stories feed is '
            "localized in that section's subpackage, not in the screen-level `Homepage.kt`. "
            "`Stories` is `home/pocket/` in the tree because nothing was renamed. Note "
            "also that `Firefox for Android` has separate Bugzilla components for several "
            "of these sections (`Top Sites`, `Stories`, `Collections`, `Bookmarks`, "
            "`Menu`, `Search`), so the same code is reachable from more than one "
            "component. Triage the bug under the component it was filed in; do not "
            "retitle or re-scope it to match."
        ),
    ),
    # The installer and the updater are triaged by the same team, so two components
    # share a channel. Keying by product-and-component rather than by channel is what
    # lets them, without either one having to know about the other.
    ScopedComponent(
        "Toolkit",
        "Application Update",
        "#installer-updater-bug-triage",
        trees=(
            "toolkit/mozapps/update/",
            "toolkit/components/maintenanceservice/",
        ),
        # The maintenance service is a separate tree, and the notes send the agent there.
        owns=(
            "toolkit/mozapps/update/",
            "toolkit/components/maintenanceservice/",
        ),
        notes=(
            "The layers the docs above describe write **separate** logs -- "
            "`update.log`, `update-elevated.log`, and the maintenance service's own "
            "`maintenanceservice.log`, the last from outside this tree "
            "(`toolkit/components/maintenanceservice/`). A reporter attaches whichever "
            "one they found, so check which log you are reading before trusting it to "
            "describe the failure, and say which layer you localized to: a status code "
            "from the updater binary is not a bug in the service that invoked it."
        ),
    ),
    ScopedComponent(
        "Firefox",
        "Installer",
        "#installer-updater-bug-triage",
        trees=("browser/installer/",),
        owns=("browser/installer/",),
        notes=(
            "The docs above list the installers; the triage question is which one the "
            "reporter ran, because they share almost no code. Note also that not all of "
            "it is NSIS: the stub's progress UI is HTML driven by JS in "
            "`browser/installer/windows/nsis/content/`, so a bug about that screen's "
            "text, layout, or high-contrast handling is localized there rather than in a "
            "`.nsi`. Coverage is thin and specific: one xpcshell test drives the "
            "**stub** only, and nothing exercises the full installer or the uninstaller. "
            "So for most Installer bugs an empty `relevant_tests` is the correct answer; "
            "say the area is uncovered rather than leaving the reader to wonder whether "
            "you looked."
        ),
    ),
)

# Where an auto-applied run reports itself, by `"<Product> :: <Component>"`. Derived, so
# that `notify.py` keeps one flat mapping to look up.
SLACK_CHANNELS = {c.key: c.channel for c in TRIAGE_SCOPE}

_SCOPE_BY_KEY = {c.key: c for c in TRIAGE_SCOPE}


def guidance_for(
    product: str | None, component: str | None
) -> tuple[ScopedComponent, ...]:
    """The components whose guidance belongs in the prompt for a bug in this component.

    **Every** component for one we do not triage, or one the caller could not determine.
    `rules/scoping.md` puts an unlisted component in scope, so guessing would leave those
    runs with less than they have today; failing open costs only the notes, which are now
    a few kilobytes rather than the whole of the deleted `rules/areas/`.
    """
    entry = _SCOPE_BY_KEY.get(
        f"{(product or '').strip()} :: {(component or '').strip()}"
    )
    if entry is None:
        return TRIAGE_SCOPE
    return (entry, *(_SCOPE_BY_KEY[key] for key in entry.related))


def _owns(owned: str, path: str) -> bool:
    """Whether an `owns` entry covers ``path``.

    The trailing slash decides how, and `test_plan.py` enforces that the spelling is
    consistent. A directory is a prefix. A file is itself and nothing else: without the
    boundary, `browser/modules/SitePermissions.sys.mjs` also claimed
    `…/SitePermissions.sys.mjs.bak`.
    """
    if owned.endswith("/"):
        return path.startswith(owned)
    return path == owned


def owners_for_path(path: str) -> tuple[ScopedComponent, ...]:
    """Every component that exclusively owns ``path``, most specific claim only.

    Empty is the common and correct answer -- it covers both a file outside the triaged
    components (`gfx/`) and ordinary desktop chrome. Read it as "no guidance is specific
    to this file", never as "guidance is missing".

    Longest match wins, so `…/fenix/home/toolbar/` is the toolbar alone even though the
    homepage claims `…/fenix/home/` and all three Android components claim
    `mobile/android/fenix/` above both.

    More than one owner comes back only for **co-ownership**, not ambiguity. Directory
    claims match by prefix, so two claims of equal length can both match a path only when
    they are the same string -- which means a plural result is always a set of components
    that deliberately declared the same entry. `hooks.component_guidance_hook` passes when
    any of them is loaded, since refusing a comment for citing a file the same team owns
    would be noise. `test_plan.py` pins that equal-length implies equal, because a third
    matching mode (globs, case folding) would break it and turn ties into length
    coincidences between unrelated paths.
    """
    best = 0
    owners: list[ScopedComponent] = []
    for entry in TRIAGE_SCOPE:
        for owned in entry.owns:
            if not _owns(owned, path):
                continue
            if len(owned) > best:
                best, owners = len(owned), [entry]
            elif len(owned) == best and entry not in owners:
                owners.append(entry)
    return tuple(owners)


# Bugzilla's `bug_severity` legal values are `--`, `blocker`, `S1`, `critical`,
# `S2`, `major`, `normal`, `S3`, `minor`, `S4`, `trivial`, `N/A`, `enhancement`
# (https://bugzilla.mozilla.org/rest/field/bug/bug_severity). Narrowed to the four
# `rules/severity-assessment.md` actually defines: the word forms are legacy, kept
# for old bugs, and `--`/`N/A` mean unset or not-applicable, which is a metadata
# regression rather than a triage judgment.
#
# The agent no longer writes the field; `agent.parse_severity` validates the level it
# suggests against this set.
TRIAGE_SEVERITIES = frozenset({"S1", "S2", "S3", "S4"})

# Which `severity_assessment.confidence` values are worth reporting. Below this the agent
# says nothing about severity at all, since a level it is unsure of still reads as a
# judgment an engineer may act on.
#
# `notify.py` reads this for the S1 marker; `rules/severity-assessment.md` repeats the
# threshold for the comment block, because the model cannot import it. Change both.
REPORTABLE_SEVERITY_CONFIDENCES = frozenset({"high", "medium"})
