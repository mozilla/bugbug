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
    # Where this component's code lives, for the prompt's index and, unless `doc_trees`
    # below overrides it, for `docs.docs_for`. Descriptive, so it may be broad and
    # overlap another component.
    trees: tuple[str, ...]
    # Where this component's documentation is registered, when that is not under `trees`.
    # Overrides `trees` for `docs.docs_for` and for nothing else: the prompt's index still
    # renders `trees`, because a docs directory is not where the code is.
    #
    # Data Sanitization is the only entry that needs it, and it needs it twice over. The
    # article is registered by `toolkit/components/antitracking/moz.build`, which is
    # `Core :: Privacy: Anti-Tracking` and not a tree this component may claim; and its own
    # `browser/base/content/sanitize*` files resolve to `browser/base/moz.build`'s
    # tabbrowser and sslerrorreport trees, so leaving the lookup on `trees` gave it two
    # unrelated components' documentation and none of its own.
    doc_trees: tuple[str, ...] = ()
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
    # tested. Everything structural belongs in the docs the trees resolve to, not here --
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
        "Sidebar",
        "#p10y-bots",
        trees=("browser/components/sidebar/",),
        owns=("browser/components/sidebar/",),
        notes=(
            "**Two sidebars ship at once, and which one the reporter saw is a build "
            "question**: `sidebar.revamp` is inside `#ifdef NIGHTLY_BUILD` in "
            "`browser/app/profile/firefox.js`, so the same steps give different UI on "
            "Nightly and on release. What that does **not** mean is two implementations "
            "to choose between. `browser/components/sidebar/browser-sidebar.js` is one "
            "`SidebarController` serving both, branching on `sidebarRevampEnabled` in 25 "
            'places, so "only with the new sidebar" is usually a branch in a shared file '
            "rather than a separate file to go and read. What is revamp-only is the lit "
            "launcher and panels (`sidebar-main.mjs` and the `sidebar-*.mjs` beside it) "
            "over `SidebarManager.sys.mjs` for global state and `SidebarState.sys.mjs` "
            "per window.\n\n"
            "**Vertical tabs is mostly not this component.** `sidebar.verticalTabs` is "
            "off by default and turning it on moves work into three other places: the "
            "strip itself is `browser/components/tabbrowser/`, the toolbar rearrangement "
            "hangs off `CustomizableUI.verticalTabsEnabled` in "
            "`browser/components/customizableui/CustomizableUI.sys.mjs`, and "
            "`browser/base/content/navigator-toolbox.js` relocates the pieces. Say which "
            "of the four you localized to; do not re-scope the bug off Sidebar for it.\n\n"
            "**Coverage is good and an empty `relevant_tests` is almost always wrong "
            "here**, but naming a file is only half the answer. 46 tests in "
            "`browser/components/sidebar/tests/browser/`, and the 6 in "
            "`browser/components/sidebar/tests/browser/legacy/` are listed twice on "
            "purpose: `browser.toml` runs them with `sidebar.revamp=false` and "
            "`browserSidebarRevamp.toml` runs the same files with it true, so cite the "
            "manifest that matches the bug. Startup and launcher behavior is covered by "
            "`browser/components/sidebar/tests/marionette/`, which a `browser_*.js` grep "
            "misses entirely. Note also that `browser/base/content/test/sidebar/` is "
            "`Firefox :: General` in `moz.build`, not this component."
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
    # Site Permissions and Data Sanitization are triaged by the same team, so they share a
    # channel, the way the installer and the updater do below.
    ScopedComponent(
        "Toolkit",
        "Data Sanitization",
        "#privacy-team-automation",
        trees=(
            "toolkit/components/cleardata/",
            "toolkit/components/forgetaboutsite/",
            "toolkit/components/clearsitedata/",
            "browser/modules/Sanitizer.sys.mjs",
            "browser/base/content/sanitizeDialog.js",
            "browser/base/content/sanitize_v2.xhtml",
        ),
        # The one entry in the registry whose documentation is not under its code: the
        # article is `toolkit/components/antitracking/docs/data-sanitization/`, registered
        # by the anti-tracking `moz.build`. See `doc_trees` on `ScopedComponent`.
        doc_trees=("toolkit/components/antitracking/docs/",),
        # The paths `moz.build` gives `BUG_COMPONENT = ("Toolkit", "Data Sanitization")`,
        # plus the test directory. Both parents are shared, so neither is claimed:
        # `browser/base/content/` holds `browser.js` and `browser/modules/` holds site
        # permissions' two modules.
        owns=(
            "toolkit/components/cleardata/",
            "toolkit/components/forgetaboutsite/",
            "toolkit/components/clearsitedata/",
            "browser/modules/Sanitizer.sys.mjs",
            "browser/base/content/sanitizeDialog.js",
            "browser/base/content/sanitize_v2.xhtml",
            "browser/base/content/test/sanitize/",
        ),
        notes=(
            "**Four pref families, and the doc above names a retired one.** Its table "
            "says `privacy.clearOnShutdown.*`; the live shutdown branch is "
            "`Sanitizer.sys.mjs`'s `PREF_SHUTDOWN_BRANCH`, which is "
            "`privacy.clearOnShutdown_v2.`, and the pre-v2 names survive only for "
            "`maybeMigratePrefs` and its "
            "`privacy.sanitize.<context>.hasMigratedToNewPrefs3` flag. The four live "
            "branches are `privacy.clearOnShutdown_v2.`, `privacy.cpd.`, "
            "`privacy.clearHistory.` and `privacy.clearSiteData.`, all declared in "
            "`browser/app/profile/firefox.js` and chosen by which entry point the user "
            "came through. So establish the entry point before reading a pref, and do "
            "not take a pref name from the doc.\n\n"
            "**Two of the five clearing entry points are not this component.** The "
            '"Manage Data" list and the identity panel\'s clear button run '
            "through `browser/modules/SiteDataManager.sys.mjs`, "
            "`browser/components/preferences/dialogs/siteDataSettings.js` and "
            "`browser/base/content/browser-siteIdentity.js`, which `moz.build` gives to "
            "`Firefox :: Settings UI`, and they reach the service without touching "
            "`Sanitizer.sys.mjs` at all. The doc lists all five together, which is what "
            "makes this worth saying: a bug about the site list or the button is not "
            "localized in the sanitizer.\n\n"
            "**A shutdown hang can be a data-clearing bug.** Clearing runs mostly on the "
            "main thread while the browser is shutting down, so a large profile can "
            "outlast the shutdown watchdog and the parent process is killed. A "
            "shutdownhang from a reporter who has clear-on-shutdown enabled localizes "
            "here rather than in the crash reporter.\n\n"
            "**Coverage is good, so an empty `relevant_tests` is almost always wrong "
            "here** -- the opposite of Installer. 23 files in "
            "`browser/base/content/test/sanitize/`, 39 under "
            "`toolkit/components/cleardata/tests/` across xpcshell, browser and "
            "marionette, and `browser/modules/test/unit/test_Sanitizer_interrupted_v2.js` "
            "for the interrupted-shutdown path. The one exception is "
            "`toolkit/components/clearsitedata/`, whose C++ header handler has no tests "
            "directory of its own because it is covered by web-platform-tests under "
            "`testing/web-platform/tests/`; say that rather than reporting no coverage."
        ),
        # Cookie permissions decide who is exempt from clear-on-shutdown and who is always
        # cleared, and site permissions owns `extensions/permissions/` where they live.
        # Settings UI is here because the notes above send the agent to the "Manage Data"
        # list and the site-data dialog to say they are *not* the sanitizer, and both are
        # paths that component owns -- without it loaded, citing either is refused.
        related=("Firefox :: Site Permissions", "Firefox :: Settings UI"),
    ),
    ScopedComponent(
        "Firefox",
        "Settings UI",
        "#fx-recomp-bots",
        # `browser/components/preferences/` claims `**`, which covers `config/`,
        # `dialogs/` and `widgets/` -- none of those declare a `BUG_COMPONENT` of their
        # own. The three modules are named one by one because `browser/modules/` is
        # mostly not this component: site permissions has two files there and
        # `Sanitizer.sys.mjs` belongs to Data Sanitization. `browser/tools/mozscreenshots/`
        # claims `preferences/**` too, and is left out as screenshot tooling.
        trees=(
            "browser/components/preferences/",
            "browser/modules/SiteDataManager.sys.mjs",
            "browser/modules/SelectionChangedMenulist.sys.mjs",
            "browser/modules/TransientPrefs.sys.mjs",
        ),
        owns=(
            "browser/components/preferences/",
            "browser/modules/SiteDataManager.sys.mjs",
            "browser/modules/SelectionChangedMenulist.sys.mjs",
            "browser/modules/TransientPrefs.sys.mjs",
        ),
        notes=(
            "Nothing under these paths registers a `SPHINX_TREES`, so there is no "
            "source doc to fall back on and the tree is the only reference.\n\n"
            "**Two settings UIs are live and the redesign is the default**, so which one "
            "the reporter saw comes before which file. `browser.settings-redesign.enabled` "
            "is `true` in `browser/app/profile/firefox.js`, and `srdSectionEnabled` in "
            "`browser/components/preferences/preferences.js` ORs it with a per-section "
            "`browser.settings-redesign.<section>.enabled`, so one pane can be new while "
            "another is old in the same profile. The legacy panes are `main.js`, "
            "`privacy.js`, `search.js`, `sync.js` and `home.js` over the `*.inc.xhtml` "
            "fragments; the redesign is declarative, one module per pane under "
            "`browser/components/preferences/config/` driven by "
            "`browser/components/preferences/config/SettingPaneManager.mjs` and "
            "`browser/components/preferences/config/SettingGroupManager.mjs` and rendered "
            "by the `setting-*` custom elements in "
            "`browser/components/preferences/widgets/`. The same control therefore exists "
            "twice, and a patch against the half the reporter was not on reads correct "
            "and changes nothing.\n\n"
            "**`about:settings` and `about:preferences` are both registered**, and a deep "
            "link carries a subcategory (`#privacy-...`) that "
            "`browser/components/preferences/config/LegacyPaneMappings.mjs`'s "
            '`resolveLegacyCategory` remaps when the redesign pref is on. So "the link '
            'took me to the wrong section" is that mapping rather than the pane it '
            "landed on.\n\n"
            "**A control that is greyed out, reset on restart, or carrying a notice is "
            "usually an add-on holding the pref**, not a defect in the pane: "
            "`browser/components/preferences/extensionControlled.js` is what puts it in "
            "that state. Check for an installed extension before localizing.\n\n"
            "**The clearing dialogs reached from the Privacy pane are `Toolkit :: Data "
            "Sanitization`**, whose guidance ships alongside this one. The line runs the "
            'other way too: the "Manage Data" site list is '
            "`browser/modules/SiteDataManager.sys.mjs` and "
            "`browser/components/preferences/dialogs/siteDataSettings.js`, which are this "
            "component even though they clear data.\n\n"
            "**Coverage is heavy, so an empty `relevant_tests` is almost always wrong** -- "
            "260 `browser_*.js` under `browser/components/preferences/tests/`. As with the "
            "panes, name the manifest and not just the file: 20 of them are duplicated as "
            "`-srd.toml`, which runs the same tests with the redesign turned on."
        ),
        related=("Toolkit :: Data Sanitization",),
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
    # Three components, one team, one channel, and their trees interleave: a chat-UI bug
    # filed under `Frontend` routinely localizes into `models/`. So all three name each
    # other in `related`, or `component_guidance_hook` refuses the comment for citing a
    # file the same team owns.
    ScopedComponent(
        "Core",
        "Machine Learning: Frontend",
        "#smart-window-bug-triage",
        trees=("browser/components/genai/", "browser/components/aiwindow/ui/"),
        owns=("browser/components/genai/", "browser/components/aiwindow/ui/"),
        related=(
            "Core :: Machine Learning: Models",
            "Core :: Machine Learning: General",
        ),
        notes=(
            "**Two unrelated UIs share this component, and almost nothing about them is "
            "the same.** `browser/components/genai/` is the third-party chatbot sidebar: "
            "`GenAI.sys.mjs` picks a provider and `chat.html` loads that provider's own "
            "web page into a browser element, so ChatGPT, Gemini, Le Chat and "
            "HuggingChat are remote documents we host rather than markup we wrote. "
            "**That is the single most common misfiling here.** A report that a button "
            "inside the ChatGPT panel is unlabelled, invisible in High Contrast, or in "
            "the wrong tab order is usually the provider's page, not our code, and the "
            "correct triage says so and stops -- do not go looking for the element in "
            "`browser/components/genai/` and do not propose a fix to a page we do not "
            "ship. What is ours in that tree is the frame around it: the provider list "
            "and prompts in `GenAI.sys.mjs`, the context-menu and shortcut entry points "
            "in `GenAIChild.sys.mjs`, and the separate Link Preview "
            "(`LinkPreview.sys.mjs`) and Page Assist (`PageAssist.sys.mjs`) features "
            "that happen to live beside it.\n\n"
            "`browser/components/aiwindow/ui/` is Smart Window, and it **is** ours all "
            "the way down -- lit custom elements under `browser/components/aiwindow/ui/"
            "components/`, actors under `browser/components/aiwindow/ui/actors/`, and "
            "the window and tab state in `browser/components/aiwindow/ui/modules/`. Work "
            "out which of the two the reporter was in before reading either; the two "
            "have no files in common.\n\n"
            "Note also that the chatbot renders inside the sidebar's frame, so the "
            "panel chrome around it -- resizing, the launcher, where the panel is "
            "docked -- is `Firefox :: Sidebar` and not this component. Coverage is good "
            "in both trees (`browser/components/genai/tests/` and "
            "`browser/components/aiwindow/ui/test/`, each with `browser/` and "
            "`xpcshell/` subdirectories), so an empty `relevant_tests` is usually wrong."
        ),
    ),
    ScopedComponent(
        "Core",
        "Machine Learning: Models",
        "#smart-window-bug-triage",
        trees=("browser/components/aiwindow/models/",),
        # `moz.build` assigns this directory to `Machine Learning: General`, and the bugs
        # filed against it arrive under this component. Both claim the same string rather
        # than one of them winning, so either team's guidance satisfies the citation hook.
        owns=("browser/components/aiwindow/models/",),
        related=(
            "Core :: Machine Learning: General",
            "Core :: Machine Learning: Frontend",
        ),
        notes=(
            "The prompt, tool and routing layer under "
            "`browser/components/aiwindow/models/`: `Chat.sys.mjs` drives a conversation, "
            "`Tools.sys.mjs` declares the tools a model may call, `PromptLoader.sys.mjs` "
            "and `PromptOptimizer.sys.mjs` assemble what is sent, and "
            "`SearchBrowsingHistory.sys.mjs` and `WCSMerinoClient.sys.mjs` are the "
            "retrieval side. `browser/components/aiwindow/models/memories/` is a separate "
            "subsystem on the same code path -- extraction, scheduling and storage of "
            "what the browser remembers about a user -- and a bug about what the model "
            "recalled is usually there rather than in the chat modules above it.\n\n"
            "**Most of what a bug here describes has no code in this tree.** Which model "
            "answered, what a provider returned, whether a search result was relevant, "
            "how good a response was: that is served remotely, and the in-tree half is "
            "only the request that provoked it. Say the behavior is not localizable in "
            "the checkout when it is not, rather than picking the nearest file that "
            "mentions the feature -- a plausible wrong file costs more than an honest "
            '"this is server-side".\n\n'
            "One trap when looking for tests: "
            "`browser/components/aiwindow/models/tests/browser_eval/` is a **model-quality "
            "evaluation harness**, one file per model behind its own `eval.toml`, not a "
            "regression suite, and it does not run in CI as one. The regression tests are "
            "`browser/components/aiwindow/models/tests/browser/` and "
            "`browser/components/aiwindow/models/tests/xpcshell/`; cite those."
        ),
    ),
    ScopedComponent(
        "Core",
        "Machine Learning: General",
        "#smart-window-bug-triage",
        trees=("browser/components/aiwindow/", "dom/modelcontext/"),
        owns=(
            "browser/components/aiwindow/",
            "browser/components/aiwindow/models/",
            "dom/modelcontext/",
        ),
        related=(
            "Core :: Machine Learning: Frontend",
            "Core :: Machine Learning: Models",
        ),
        notes=(
            "The catch-all of the three, and it spans two eras of the same product. Old "
            "chatbot-sidebar reports still arrive here rather than under `Machine "
            "Learning: Frontend` -- the two components were used interchangeably for that "
            "UI for a year -- so the component name does not tell you which tree, and a "
            "2024 or 2025 bug about a provider panel is `browser/components/genai/` even "
            "though nothing here points at it. Newer bugs are the Smart Window plumbing "
            "that is neither the UI nor the model layer: what sits at the root of "
            "`browser/components/aiwindow/`, and the Model Context Protocol surface in "
            "`dom/modelcontext/`.\n\n"
            "Two neighbors this is regularly confused with, neither of them triaged here. "
            "The on-device inference runtime -- model download, the WASM engine, the model "
            "cache -- is `toolkit/components/ml/`, filed as `Machine Learning: On Device`. "
            "The prompt and tool layer is `Machine Learning: Models`, which owns "
            "`browser/components/aiwindow/models/` alongside this component. Triage the "
            "bug under the component it was filed in and say where the code turned out to "
            "be; do not retitle or re-scope it to match."
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
