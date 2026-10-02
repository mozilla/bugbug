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

# mozregression bisection tool (in-process MCP server "mozregression"). Only the
# `bisector` subagent calls it, and only when the run has bisection on.
MOZREGRESSION_TOOLS = [
    "mcp__mozregression__run_mozregression",
]

# Added to `ENABLED_ACTION_TYPES` only when bisection is on, so a run that cannot find a
# range cannot record a field change either. `update_bug_hook` limits it to the three
# regression-range fields. It brings back the `editbugs` requirement described above.
BISECT_ACTION_TYPES = [
    "bugzilla.update_bug",
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
    # Two components **may** declare the same entry, and the Android ones all do: that
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
        # Every Android component claims the shared trees so that a bug localized
        # anywhere in Fenix reaches an Android team, and the narrower entries below
        # still win by length. Before this was plural, most of `mobile/android/` was
        # owned by nobody and the citation hook stopped firing there.
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
    ScopedComponent(
        "Firefox for Android",
        "Downloads",
        "#android-core-dev",
        trees=(
            "mobile/android/fenix/",
            "mobile/android/android-components/",
        ),
        owns=(
            "mobile/android/fenix/",
            "mobile/android/android-components/",
            "mobile/android/fenix/app/src/main/java/org/mozilla/fenix/downloads/",
            "mobile/android/fenix/app/src/main/java/org/mozilla/fenix/settings/downloads/",
            "mobile/android/android-components/components/feature/downloads/",
        ),
        notes=(
            "**The download is android-components; Fenix supplies dependencies and "
            "draws the list.** A response GeckoView will not render arrives through "
            "`onExternalResponse` in "
            "`mobile/android/android-components/components/browser/engine-gecko/src/main/java/mozilla/components/browser/engine/gecko/GeckoEngineSession.kt` "
            "as a `DownloadState`. From there `DownloadsFeature` shows the prompt, "
            "`FetchDownloadManager` checks permissions, and "
            "`AbstractFetchDownloadService` writes the file and owns the notification, "
            "all in `mobile/android/android-components/components/feature/downloads/`. "
            "Fenix's `DownloadService` only overrides that service's dependencies, and "
            "its `httpClient` is `GeckoViewFetchClient`, so the bytes come through "
            "Gecko's network stack either way. So a stalled, truncated or misnamed "
            'file, a stuck notification, or "tapping Download does nothing" localizes '
            "in android-components, not Fenix. Two traps there: "
            "`FetchDownloadManager.download` returns `null` for any scheme other than "
            "`http`, `https`, `data`, `blob` and `moz-extension`, and it only asks for "
            "`WRITE_EXTERNAL_STORAGE` below Android 10 (API 29), so a "
            "storage-permission report is only plausible on old devices.\n\n"
            "**The list is a view over `BrowserState.downloads`, and it prunes "
            "itself.** "
            "`mobile/android/fenix/app/src/main/java/org/mozilla/fenix/downloads/listscreen/` "
            "is the only list implementation (Compose), and opening it dispatches "
            "`RemoveDeletedDownloads`, which drops every completed or cancelled entry "
            'whose file no longer exists. "My download vanished from the list" is '
            "therefore usually a file removed outside Firefox rather than a list bug. "
            "PDFs are the other trap: `pdfjs.handleOctetStream` is on in "
            "`mobile/android/app/geckoview-prefs.js`, so a PDF link opens inline in "
            "pdf.js instead of downloading, and both pdf.js's download button and Save "
            "as PDF go through `requestPdfToDownload` in `GeckoEngineSession.kt`, "
            "which deliberately reports a content length of 0, so no size is shown. A "
            "bug in the viewer itself is "
            "`mobile/android/fenix/app/src/main/java/org/mozilla/fenix/pdf/` or "
            "pdf.js, which is `Firefox :: PDF Viewer`, not this component.\n\n"
            "**Coverage is good at both layers**: 21 unit test files in "
            "`mobile/android/android-components/components/feature/downloads/src/test/`, "
            "12 in "
            "`mobile/android/fenix/app/src/test/java/org/mozilla/fenix/downloads/` and "
            "2 in "
            "`mobile/android/fenix/app/src/test/java/org/mozilla/fenix/settings/downloads/`, "
            "plus the UI tests "
            "`mobile/android/fenix/app/src/androidTest/java/org/mozilla/fenix/ui/DownloadTest.kt` "
            "and "
            "`mobile/android/fenix/app/src/androidTest/java/org/mozilla/fenix/ui/DownloadFileTypesTest.kt`, "
            "each duplicated under "
            "`mobile/android/fenix/app/src/androidTest/java/org/mozilla/fenix/ui/efficiency/tests/`."
        ),
    ),
    ScopedComponent(
        "Firefox for Android",
        "Tabs",
        "#android-core-dev",
        trees=(
            "mobile/android/fenix/",
            "mobile/android/android-components/",
        ),
        owns=(
            "mobile/android/fenix/",
            "mobile/android/android-components/",
            "mobile/android/fenix/app/src/main/java/org/mozilla/fenix/tabstray/",
            "mobile/android/fenix/app/src/main/java/org/mozilla/fenix/tabgroups/",
            "mobile/android/fenix/app/src/main/java/org/mozilla/fenix/tabhistory/",
            "mobile/android/android-components/components/feature/tabs/",
            "mobile/android/android-components/components/feature/tabgroups-storage/",
            "mobile/android/android-components/components/feature/tabdata-coordinator/",
            "mobile/android/android-components/components/browser/tabstray/",
        ),
        notes=(
            "**The tabs tray is Compose, and android-components' tray is not what "
            "Fenix runs.** The live screen is "
            "`mobile/android/fenix/app/src/main/java/org/mozilla/fenix/tabstray/ui/tabstray/TabsTray.kt`, "
            "with one page per section under "
            "`mobile/android/fenix/app/src/main/java/org/mozilla/fenix/tabstray/ui/tabpage/`. "
            "The View-based `TabsAdapter` in "
            "`mobile/android/android-components/components/browser/tabstray/` and "
            "`TabsFeature` in "
            "`mobile/android/android-components/components/feature/tabs/` are not "
            "built by Fenix, which takes only strings and `TabThumbnailView` from the "
            "first and `TabsUseCases` from the second, so a rendering fix planned "
            "there changes nothing. Tab state is shared: `TabSessionState` in "
            "`mobile/android/android-components/components/browser/state/` holds every "
            'tab, and a private tab is just `content.private`. "Inactive" is not '
            "stored at all: `isNormalTabInactive` derives it from `lastActiveTime` "
            "against a fixed 14 days (`DEFAULT_ACTIVE_DAYS` in "
            "`mobile/android/fenix/app/src/main/java/org/mozilla/fenix/ext/BrowserState.kt`), "
            "and only when `inactiveTabsAreEnabled` is set. Note also that "
            "`mobile/android/fenix/app/src/main/java/org/mozilla/fenix/tabhistory/` is "
            "not the tray: it is the long-press Back history list, still View-based, "
            "and the tablet tab strip is "
            "`mobile/android/fenix/app/src/main/java/org/mozilla/fenix/browser/tabstrip/`.\n\n"
            "**Tab groups have their own storage, and drag-and-drop forks on a "
            "per-channel flag.** Group membership is not on `TabSessionState`; it is "
            "persisted by the Room-backed `TabGroupRepository` in "
            "`mobile/android/android-components/components/feature/tabgroups-storage/` "
            "and joined to tabs by "
            "`mobile/android/android-components/components/feature/tabdata-coordinator/`, "
            'so "a tab left its group after restart" is that storage rather than the '
            "tray. In `mobile/android/fenix/app/nimbus.fml.yaml`, `tab-groups` "
            "defaults on but `tab-groups-drag-and-drop` is off on release and on in "
            "beta and nightly, and debug builds force it on via "
            "`DefaultTabManagementFeatureHelper`. That flag picks the code: "
            "`mobile/android/fenix/app/src/main/java/org/mozilla/fenix/tabstray/ui/tabpage/TabLayout.kt` "
            "renders the `Interactable*` grid and list when it is on and the "
            "`Reorderable*` ones from "
            "`mobile/android/fenix/app/src/main/java/org/mozilla/fenix/tabstray/browser/compose/legacy/` "
            "when it is off, and the private page always passes `false`. A reorder bug "
            "on release and the same report on Nightly are different code, so "
            "establish the channel first.\n\n"
            "**Coverage is good, so an empty `relevant_tests` is usually wrong**: 38 "
            "unit test files in "
            "`mobile/android/fenix/app/src/test/java/org/mozilla/fenix/tabstray/`, 12 "
            "in `mobile/android/fenix/app/src/test/java/org/mozilla/fenix/tabgroups/`, "
            "Compose tests in "
            "`mobile/android/fenix/app/src/androidTest/java/org/mozilla/fenix/tabstray/ui/` "
            "and "
            "`mobile/android/fenix/app/src/androidTest/java/org/mozilla/fenix/tabgroups/`, "
            "and 9 in "
            "`mobile/android/android-components/components/feature/tabs/src/test/`."
        ),
    ),
    ScopedComponent(
        "Firefox for Android",
        "Translations",
        "#android-core-dev",
        trees=(
            "mobile/android/fenix/",
            "mobile/android/android-components/",
        ),
        owns=(
            "mobile/android/fenix/",
            "mobile/android/android-components/",
            "mobile/android/fenix/app/src/main/java/org/mozilla/fenix/translations/",
        ),
        notes=(
            "**Fenix draws the controls; Gecko does the translating.** A request "
            "passes from "
            "`mobile/android/fenix/app/src/main/java/org/mozilla/fenix/translations/` "
            "through the engine-neutral types in "
            "`mobile/android/android-components/components/concept/engine/src/main/java/mozilla/components/concept/engine/translate/`, "
            "the adapter in "
            "`mobile/android/android-components/components/browser/engine-gecko/src/main/java/mozilla/components/browser/engine/gecko/translate/`, "
            "GeckoView's "
            "`mobile/android/geckoview/src/main/java/org/mozilla/geckoview/TranslationsController.java` "
            "and `mobile/shared/modules/geckoview/GeckoViewTranslations.sys.mjs`, into "
            "the engine desktop also uses, `toolkit/components/translations/`. So a "
            "wrong, garbled or partial translation, a missing language pair, or a "
            "model that will not download is the engine or its data, not Fenix: "
            "`toolkit/components/translations/actors/TranslationsParent.sys.mjs` "
            "fetches both the models and the WASM from Remote Settings, and a Fenix "
            "patch cannot fix either. Language detection and the decision to offer are "
            "Gecko's too (`TranslationsParent:OfferTranslation`, forwarded by "
            'GeckoView), so "Firefox did not offer to translate" starts there, after '
            "ruling out the user's never-translate settings.\n\n"
            "**Part of the Fenix surface sits outside that package, and two switches "
            "can hide all of it.** The bottom sheet and the settings screens are in "
            "the translations package, but the offer banner and page status are wired "
            "from "
            "`mobile/android/fenix/app/src/main/java/org/mozilla/fenix/browser/TranslationsBinding.kt` "
            "and "
            "`mobile/android/fenix/app/src/main/java/org/mozilla/fenix/browser/TranslationsBannerIntegration.kt`, "
            "and the main-menu item is "
            "`mobile/android/fenix/app/src/main/java/org/mozilla/fenix/components/menu/compose/TranslationMenuItem.kt`, "
            "which disables itself in Reader View and on PDFs. Per-tab state is "
            "`TranslationsState` in "
            '`mobile/android/android-components/components/browser/state/`. "The '
            'Translate option is missing" may mean `TranslationsEnabledSettings` was '
            "turned off, or that `TranslationsParent.getIsTranslationsEngineSupported` "
            "found no WASM SIMD on the device, which reports the engine unsupported "
            "and is expected, not a defect.\n\n"
            "**Tests are split by layer**: 8 unit test files in "
            "`mobile/android/fenix/app/src/test/java/org/mozilla/fenix/translations/` "
            "plus "
            "`mobile/android/fenix/app/src/test/java/org/mozilla/fenix/browser/TranslationsBindingTest.kt`, "
            "`mobile/android/geckoview/src/androidTest/java/org/mozilla/geckoview/test/TranslationsTest.kt` "
            "for the GeckoView bridge, and 55 `browser_*.js` in "
            "`toolkit/components/translations/tests/browser/`, which run on desktop "
            "but exercise the same engine."
        ),
    ),
    ScopedComponent(
        "Firefox for Android",
        "QR",
        "#android-activation-and-trust",
        trees=(
            "mobile/android/fenix/",
            "mobile/android/android-components/",
        ),
        owns=(
            "mobile/android/fenix/",
            "mobile/android/android-components/",
            "mobile/android/android-components/components/feature/qr/",
            "mobile/android/fenix/app/src/main/java/org/mozilla/fenix/components/appstate/qrScanner/",
            "mobile/android/fenix/app/src/main/java/org/mozilla/fenix/components/QrScanFenixFeature.kt",
            "mobile/android/fenix/app/src/main/java/org/mozilla/fenix/components/QrScanConfirmation.kt",
            "mobile/android/fenix/app/src/main/java/org/mozilla/fenix/components/menu/share/QRCodeDialogFragment.kt",
            "mobile/android/fenix/app/src/main/java/org/mozilla/fenix/components/menu/share/QRCodeDownloader.kt",
            "mobile/android/fenix/app/src/main/java/org/mozilla/fenix/components/menu/share/QRCodeGenerator.kt",
        ),
        notes=(
            "**Scanning and generating share nothing, and scanning has two cameras.** "
            "Reading a code from the toolbar's scan button goes through "
            "`mobile/android/fenix/app/src/main/java/org/mozilla/fenix/components/QrScanFenixFeature.kt`, "
            "which launches android-components' `QrScanActivity`; that hosts "
            "`QrFragment` on Camera2 and decodes with ZXing in `QrAnalyzer`, all in "
            "`mobile/android/android-components/components/feature/qr/`. The Google "
            "Lens camera in "
            "`mobile/android/fenix/app/src/main/java/org/mozilla/fenix/components/lens/` "
            "has its own QR mode and gallery picker that reuse `QrAnalyzer` and hand "
            "the result to the same `handleToolbarQrScanResults`. So a camera, preview "
            "or permission bug depends on which button the reporter tapped, while a "
            "code that will not decode is `QrAnalyzer` either way. Sync pairing uses "
            "the older `QrFeature` from the same module, not either of these.\n\n"
            "**A scan never loads a page by itself.** `handleToolbarQrScanResults` "
            "normalizes the string, shows an invalid-URL dialog for anything that is "
            "not `http` or `https` (a `tel:` or `WIFI:` code, for example), asks for "
            "confirmation, and then `BrowserToolbarSearchMiddleware` in "
            "`mobile/android/fenix/app/src/main/java/org/mozilla/fenix/search/` "
            "pre-fills the toolbar for the user to submit. Both are deliberate, not "
            "decode bugs. Generating a code is `QRCodeGenerator.kt`, "
            "`QRCodeDialogFragment.kt` and `QRCodeDownloader.kt` in "
            "`mobile/android/fenix/app/src/main/java/org/mozilla/fenix/components/menu/share/`, "
            "reachable only as a chooser action that "
            "`mobile/android/fenix/app/src/main/java/org/mozilla/fenix/components/share/ShareSheetLauncher.kt` "
            "adds on Android 14 (API 34) and later; on older devices there is no QR "
            "option, and that is expected.\n\n"
            "**Coverage is thin and mostly not under a QR name**: 7 unit test files in "
            "`mobile/android/android-components/components/feature/qr/src/test/`, and "
            "in Fenix `QRCodeDownloaderTest.kt` and `ShareSheetLauncherTest.kt` under "
            "`mobile/android/fenix/app/src/test/java/org/mozilla/fenix/components/menu/` "
            "plus "
            "`mobile/android/fenix/app/src/test/java/org/mozilla/fenix/components/appstate/qrscanner/QrScannerActionTest.kt`. "
            "The end-to-end scan and camera-permission tests are "
            "`scanQRCodeToOpenAWebpageTest` and "
            "`verifyQRScanningCameraAccessDialogTest` in "
            "`mobile/android/fenix/app/src/androidTest/java/org/mozilla/fenix/ui/efficiency/tests/SearchTest.kt`. "
            "Nothing tests `QrScanFenixFeature` or `QRCodeGenerator` directly."
        ),
    ),
    ScopedComponent(
        "Firefox for Android",
        "Experimentation and Telemetry",
        "#android-activation-and-trust",
        trees=(
            "mobile/android/fenix/",
            "mobile/android/android-components/",
        ),
        # Not `metrics.yaml`, `pings.yaml` or `nimbus.fml.yaml`: every feature declares its
        # metrics and flags there, and other components' guidance cites them.
        owns=(
            "mobile/android/fenix/",
            "mobile/android/android-components/",
            "mobile/android/fenix/app/src/main/java/org/mozilla/fenix/experiments/",
            "mobile/android/fenix/app/src/main/java/org/mozilla/fenix/nimbus/",
            "mobile/android/fenix/app/src/main/java/org/mozilla/fenix/settings/labs/nimbus/",
            "mobile/android/fenix/app/src/main/java/org/mozilla/fenix/telemetry/",
            "mobile/android/fenix/app/src/main/java/org/mozilla/fenix/components/metrics/",
            "mobile/android/fenix/app/src/main/java/org/mozilla/fenix/settings/datachoices/",
            "mobile/android/fenix/app/src/main/java/org/mozilla/fenix/settings/studies/",
            "mobile/android/android-components/components/service/nimbus/",
            "mobile/android/android-components/components/service/glean/",
        ),
        notes=(
            "**A metric that never arrives is usually its call site, not this "
            "component.** The metrics in `mobile/android/fenix/app/metrics.yaml` are "
            "recorded directly from well over a hundred files across every feature "
            "package, so read the feature that should have recorded it before the "
            "plumbing. android-components is the exception: its components emit "
            "`Facts` instead of calling Glean, and one `when` in "
            "`mobile/android/fenix/app/src/main/java/org/mozilla/fenix/components/metrics/MetricController.kt` "
            "maps them to metrics. Only `ReleaseMetricController` registers that "
            "processor, and the debug build type sets `TELEMETRY` to `false` in "
            "`mobile/android/fenix/app/build.gradle`, so on a local debug build "
            "AC-originated metrics never fire while direct calls still do. Two gates "
            "also look like bugs: on a fresh install Glean is not initialized until "
            "the onboarding terms-of-service card is accepted, and turning usage data "
            "off in data choices stops collection and sets `experimentParticipation` "
            "to false with it. Gecko's own metrics are not in this YAML at all; they "
            "are listed in `gecko_metrics` in "
            "`toolkit/components/glean/metrics_index.py`.\n\n"
            "**An experiment is mostly data that is not in the tree.** "
            "`mobile/android/fenix/app/nimbus.fml.yaml` declares the features and "
            "their in-tree defaults, and many carry per-channel defaults (mostly "
            'nightly and developer), so "works on Nightly but not Release" is often a '
            "default rather than code. Branch values come from Remote Settings, and "
            "fetched data is normally not applied in the session that fetched it. "
            "First run reads "
            "`mobile/android/fenix/app/src/main/res/raw/initial_experiments.json`, a "
            "snapshot refreshed by the periodic `repo-update` commits, so check it for "
            "what a new install could be enrolled in before calling a definition "
            'unreadable. "I never got the experiment" is targeting, timing or a '
            "studies/telemetry opt-out far more often than a defect in "
            "`mobile/android/fenix/app/src/main/java/org/mozilla/fenix/experiments/NimbusSetup.kt`. "
            "One Firefox Labs trap: `toLabsItem` in "
            "`mobile/android/fenix/app/src/main/java/org/mozilla/fenix/settings/labs/nimbus/FirefoxLabsMetadata.kt` "
            "drops a Lab whose string resource the build lacks, but on Nightly and "
            "debug shows the raw resource name instead, so the same Lab can be visible "
            "on one channel and missing on another.\n\n"
            "**Glean assertions live in ordinary unit tests, not a telemetry suite.** "
            "About 90 files under `mobile/android/fenix/app/src/test/` use "
            "`GleanTestRule`, spread across `home`, `tabstray`, `settings` and the "
            "rest, so the regression test for a metric bug belongs next to the "
            "feature's own test and an empty `relevant_tests` is usually wrong. The "
            "plumbing has its own in "
            "`mobile/android/fenix/app/src/test/java/org/mozilla/fenix/components/metrics/` "
            "(including `MetricControllerTest.kt` for the fact mapping) and "
            "`mobile/android/fenix/app/src/test/java/org/mozilla/fenix/experiments/`. "
            "The android-components wrapper is tested in "
            "`mobile/android/android-components/components/service/nimbus/src/test/`; "
            "the SDK itself is vendored at "
            "`third_party/application-services/components/nimbus/`."
        ),
    ),
    ScopedComponent(
        "Firefox for Android",
        "Logins",
        "#android-activation-and-trust",
        trees=(
            "mobile/android/fenix/",
            "mobile/android/android-components/",
        ),
        # Not `feature/autofill/` or Fenix's `autofill/`, which serve addresses and cards too.
        owns=(
            "mobile/android/fenix/",
            "mobile/android/android-components/",
            "mobile/android/fenix/app/src/main/java/org/mozilla/fenix/settings/logins/",
            "mobile/android/android-components/components/feature/logins/",
            "mobile/android/android-components/components/service/sync-logins/",
        ),
        notes=(
            "**Two autofill paths share one settings screen and almost no code.** "
            "Filling a login on a web page is Gecko: Gecko's password manager finds "
            "the form, "
            "`mobile/shared/components/geckoview/LoginStorageDelegate.sys.mjs` and "
            "`mobile/android/geckoview/src/main/java/org/mozilla/geckoview/Autocomplete.java` "
            "carry the request out, "
            "`mobile/android/android-components/components/service/sync-logins/src/main/java/mozilla/components/service/sync/logins/GeckoLoginStorageDelegate.kt` "
            "answers it from storage, and the select bar and save/update dialog are "
            "`mobile/android/android-components/components/feature/prompts/src/main/java/mozilla/components/feature/prompts/login/`. "
            "Filling a login in another app is Android's autofill framework, served by "
            "`mobile/android/fenix/app/src/main/java/org/mozilla/fenix/autofill/AutofillService.kt` "
            "over `mobile/android/android-components/components/feature/autofill/`, "
            "with its own unlock activity. The passwords screen has one toggle for "
            "each (`pref_key_autofill_logins`, which sets GeckoView's "
            "`loginAutofillEnabled`, and `pref_key_android_autofill`), so establish "
            "which one the reporter means first. A form Firefox does not recognize, or "
            "a login offered on the wrong site, is usually the Gecko half, which "
            "`moz.build` gives to `Toolkit :: Password Manager`.\n\n"
            '**"My passwords are gone" is storage, and one path deletes them on '
            "purpose.** Saved logins live in application-services' Rust store "
            "(`third_party/application-services/components/logins/`), encrypted with a "
            "key that "
            "`mobile/android/android-components/components/service/sync-logins/src/main/java/mozilla/components/service/sync/logins/LoginsCrypto.kt` "
            "keeps in Keystore-backed secure preferences. When that key is lost or "
            "fails its canary check, `recoverFromKeyLoss` calls `wipeLocal()`, and "
            "only Sync brings the logins back. Fenix also sets "
            '`android:allowBackup="false"` in '
            "`mobile/android/fenix/app/src/main/AndroidManifest.xml`, so a new phone "
            "has no logins without Sync either. Ask whether the reporter uses Sync "
            'before reading any UI. "Never save" exceptions are a separate Room '
            "database in "
            "`mobile/android/android-components/components/feature/logins/`, and their "
            "screen is "
            "`mobile/android/fenix/app/src/main/java/org/mozilla/fenix/exceptions/login/`, "
            "not the logins package.\n\n"
            "**The biometric gate is split across two packages, and a helper name is "
            "duplicated.** The list is the Compose "
            "`mobile/android/fenix/app/src/main/java/org/mozilla/fenix/settings/logins/ui/SavedLoginsScreen.kt`, "
            "wrapped in `SecureScreen` from "
            "`mobile/android/fenix/app/src/main/java/org/mozilla/fenix/settings/biometric/ui/SecureScreen.kt`, "
            "which calls back into `BiometricAuthenticationHelper.kt` in the logins "
            "package. Two objects are named `DefaultBiometricUtils`, one in "
            "`mobile/android/fenix/app/src/main/java/org/mozilla/fenix/settings/logins/ui/BiometricAuthenticationUtils.kt` "
            "and one in "
            "`mobile/android/fenix/app/src/main/java/org/mozilla/fenix/settings/biometric/BiometricUtils.kt` "
            "(used by the private-browsing lock), so check the import before citing "
            "either. Tests: 5 unit tests in "
            "`mobile/android/fenix/app/src/test/java/org/mozilla/fenix/settings/logins/`, "
            "5 for the prompts, 3 each in sync-logins and feature/autofill, "
            "`mobile/android/geckoview/src/androidTest/java/org/mozilla/geckoview/test/AutocompleteTest.kt` "
            "for the Gecko half, and UI tests twice over: "
            "`mobile/android/fenix/app/src/androidTest/java/org/mozilla/fenix/ui/LoginsTest.kt` "
            "and "
            "`mobile/android/fenix/app/src/androidTest/java/org/mozilla/fenix/ui/efficiency/tests/LoginsTest.kt`."
        ),
        related=("Firefox for Android :: Settings",),
    ),
    ScopedComponent(
        "Firefox for Android",
        "Onboarding",
        "#android-activation-and-trust",
        trees=(
            "mobile/android/fenix/",
            "mobile/android/android-components/",
        ),
        owns=(
            "mobile/android/fenix/",
            "mobile/android/android-components/",
            "mobile/android/fenix/app/src/main/java/org/mozilla/fenix/onboarding/",
            "mobile/android/fenix/app/onboarding.fml.yaml",
        ),
        notes=(
            "**What the reporter saw is mostly configuration, and the in-tree default "
            "is small.** The upfront cards are the `cards` map of `juno-onboarding` in "
            "`mobile/android/fenix/app/onboarding.fml.yaml`, filtered and ordered by "
            "`mobile/android/fenix/app/src/main/java/org/mozilla/fenix/onboarding/view/OnboardingMapper.kt`. "
            "The default ships three cards (terms of service, default browser, "
            "marketing data); sign-in, notification permission, search widget and "
            "toolbar placement are valid card types that only appear when an "
            "experiment supplies them, and since onboarding runs before any fetch, "
            "`mobile/android/fenix/app/src/main/res/raw/initial_experiments.json` is "
            "what a fresh install can be enrolled in. So a page, wording or order that "
            "is not in the default is an experiment branch; say which, rather than "
            "hunting for code that is not there. Cards also hide themselves by device "
            "state in `OnboardingFragment.kt`: default browser when Firefox already "
            "is, notification permission below Android 13 or when notifications are "
            "on, the search widget on every Xiaomi device, and any card whose "
            "`prerequisites` name a condition missing from `conditions` is dropped "
            "silently.\n\n"
            "**Onboarding does not end at the last card.** `continuous-onboarding` is "
            "on by default and "
            "`mobile/android/fenix/app/src/main/java/org/mozilla/fenix/onboarding/continuous/ContinuousOnboardingFeature.kt` "
            "asks for the default-browser role on day 2 or 3, shows a sync card on day "
            "5 if the user is signed out, and an IP Protection prompt on day 7, "
            "started from the homepage rather than the onboarding fragment. A "
            "default-browser or sign-in prompt days after install is this, not a "
            "Nimbus message; the homescreen message cards are "
            "`mobile/android/fenix/app/messaging-evergreen-messages.fml.yaml` over "
            "`mobile/android/fenix/app/src/main/java/org/mozilla/fenix/messaging/`, a "
            'different system with a `default-browser` message of its own. For "Set as '
            'default does nothing", `openSetDefaultBrowserOption` in '
            "`mobile/android/fenix/app/src/main/java/org/mozilla/fenix/ext/Activity.kt` "
            "uses Android's `RoleManager` dialog on Android 10 and later, then the "
            "system default-apps settings, then a SUMO page.\n\n"
            "**It does not run on a local debug build**: `onboardingFeatureEnabled` is "
            "`!Config.channel.isDebug` in "
            "`mobile/android/fenix/app/src/main/java/org/mozilla/fenix/FeatureFlags.kt`, "
            "and whether it shows again is `CURRENT_ONBOARDING_VERSION` in "
            "`FenixOnboarding.kt`. Glean also waits on this component, since a new "
            "install initializes it only after the terms-of-service card is accepted, "
            "so an onboarding metric bug should first establish whether the event "
            "fires before or after that point. Coverage is good: 18 unit tests under "
            "`mobile/android/fenix/app/src/test/java/org/mozilla/fenix/onboarding/`, "
            "including `OnboardingMapperTest.kt`, which has a same-named instrumented "
            "twin in "
            "`mobile/android/fenix/app/src/androidTest/java/org/mozilla/fenix/onboarding/view/`, "
            "and UI tests in both "
            "`mobile/android/fenix/app/src/androidTest/java/org/mozilla/fenix/ui/OnboardingTest.kt` "
            "and "
            "`mobile/android/fenix/app/src/androidTest/java/org/mozilla/fenix/ui/efficiency/tests/OnboardingTest.kt`."
        ),
    ),
    ScopedComponent(
        "Firefox for Android",
        "Privacy",
        "#android-activation-and-trust",
        trees=(
            "mobile/android/fenix/",
            "mobile/android/android-components/",
        ),
        owns=(
            "mobile/android/fenix/",
            "mobile/android/android-components/",
            "mobile/android/fenix/app/src/main/java/org/mozilla/fenix/trackingprotection/",
            "mobile/android/fenix/app/src/main/java/org/mozilla/fenix/privacyreport/",
            "mobile/android/fenix/app/src/main/java/org/mozilla/fenix/home/privatebrowsing/",
            "mobile/android/fenix/app/src/main/java/org/mozilla/fenix/pbmlock/",
            "mobile/android/fenix/app/src/main/java/org/mozilla/fenix/settings/deletebrowsingdata/",
            "mobile/android/fenix/app/src/main/java/org/mozilla/fenix/settings/trustpanel/",
            "mobile/android/android-components/components/feature/privatemode/",
            "mobile/android/android-components/components/feature/protection-dashboard/",
        ),
        notes=(
            "**Enhanced Tracking Protection is configured in Fenix and enforced in "
            "Gecko.** The settings screen is "
            "`mobile/android/fenix/app/src/main/java/org/mozilla/fenix/settings/TrackingProtectionFragment.kt` "
            "over "
            "`mobile/android/fenix/app/src/main/res/xml/tracking_protection_preferences.xml`, "
            "owned by Settings even though the feature is this component's. "
            "`mobile/android/fenix/app/src/main/java/org/mozilla/fenix/components/TrackingProtectionPolicyFactory.kt` "
            "turns the Standard/Strict/Custom choice into a policy, and "
            "`mobile/android/android-components/components/browser/engine-gecko/` "
            "hands it to GeckoView's `ContentBlocking`. What actually gets blocked "
            "after that is Gecko's classifier and lists "
            '(`toolkit/components/antitracking/`, `netwerk/url-classifier/`), so "a '
            'site breaks with ETP on" or "a tracker was not blocked" is almost never '
            "Fenix; the Fenix question is only whether the policy built matches what "
            "the user chose, such as Custom's private-tabs-only branch in "
            "`createCustomTrackingProtectionPolicy`. Per-site exceptions go through "
            "`GeckoTrackingProtectionExceptionStorage.kt` into Gecko's permission "
            "store via GeckoView's `StorageController`, not into a Fenix database.\n\n"
            "**There is one site panel now, and it is not only protections.** The "
            "toolbar's site icon opens "
            "`mobile/android/fenix/app/src/main/java/org/mozilla/fenix/settings/trustpanel/TrustPanelFragment.kt` "
            "for normal and custom tabs alike; it also carries connection security, "
            "the site's permissions and a clear-site-data dialog, so a permission or "
            "certificate bug reported from that panel localizes in its store and "
            "middleware rather than in ETP code. Tracker counts on the homepage, tabs "
            "tray, trust panel and Protections dashboard are all read by "
            "`mobile/android/fenix/app/src/main/java/org/mozilla/fenix/trackingprotection/TrackersBlockedFeature.kt` "
            "from Gecko's tracking database, not tab state, so a count wrong "
            "everywhere is Gecko's and one wrong on a single surface is that surface.\n\n"
            "**Private browsing and data deletion are several separate pieces.** "
            "Screenshot blocking and the private-tabs notification are "
            "`mobile/android/android-components/components/feature/privatemode/`; the "
            "biometric lock is "
            "`mobile/android/fenix/app/src/main/java/org/mozilla/fenix/pbmlock/`, "
            "toggled from "
            "`mobile/android/fenix/app/src/main/java/org/mozilla/fenix/settings/PrivateBrowsingFragment.kt`, "
            "which Settings owns. Delete browsing data on quit runs only from the "
            "menu's Quit item, which "
            "`mobile/android/fenix/app/src/main/java/org/mozilla/fenix/components/menu/MenuDialogFragment.kt` "
            'shows only while the option is on, so "data survived after I swiped the '
            'app away" is expected; the downloads option removes list entries, not '
            "files. Tests: about 20 unit tests across this component's Fenix packages, "
            "plus "
            "`mobile/android/fenix/app/src/test/java/org/mozilla/fenix/components/TrackingProtectionPolicyFactoryTest.kt` "
            "and "
            "`mobile/android/fenix/app/src/test/java/org/mozilla/fenix/settings/TrackingProtectionFragmentTest.kt`, "
            "and UI tests in both generations, e.g. "
            "`mobile/android/fenix/app/src/androidTest/java/org/mozilla/fenix/ui/UnifiedTrustPanelTest.kt` "
            "and "
            "`mobile/android/fenix/app/src/androidTest/java/org/mozilla/fenix/ui/efficiency/tests/UnifiedTrustPanelTest.kt`."
        ),
        related=("Firefox for Android :: Settings",),
    ),
    ScopedComponent(
        "Firefox for Android",
        "Settings",
        "#android-activation-and-trust",
        trees=(
            "mobile/android/fenix/",
            "mobile/android/android-components/",
        ),
        # The whole package, with the subpackages other components claim winning by length.
        owns=(
            "mobile/android/fenix/",
            "mobile/android/android-components/",
            "mobile/android/fenix/app/src/main/java/org/mozilla/fenix/settings/",
        ),
        notes=(
            "**The row list is not the XML.** The top level is "
            "`mobile/android/fenix/app/src/main/java/org/mozilla/fenix/settings/SettingsFragment.kt` "
            "over `mobile/android/fenix/app/src/main/res/xml/preferences.xml`, and "
            "most sub-screens follow the same pattern over another preference XML file "
            "beside it, while newer ones (data choices, Firefox Labs, the passwords "
            "list) are Compose. Many rows in `preferences.xml` are declared "
            '`isPreferenceVisible="false"` and turned on in code, the debug, Nimbus '
            "and profiler rows only when `showSecretDebugMenuThisSession` is set, so "
            '"a row is missing" is usually a condition in `SettingsFragment.kt` rather '
            "than the XML. What a toggle stores is "
            "`mobile/android/fenix/app/src/main/java/org/mozilla/fenix/utils/Settings.kt`, "
            "`SharedPreferences` delegates keyed by the same `pref_key_*` strings the "
            "XML uses; dozens of them read `FxNimbus`, so a default that differs "
            "between two users on one build is often an experiment "
            "(`mobile/android/fenix/app/nimbus.fml.yaml`) rather than code. Settings "
            "search is a separate index too: "
            "`mobile/android/fenix/app/src/main/java/org/mozilla/fenix/settings/settingssearch/` "
            "parses only the XML files listed in `PreferenceFileInformation.kt` and "
            "gets Compose screens' entries through `SettingsSearchProvider`. So "
            '"search does not find setting X" or "the result opens the wrong screen" '
            "is that list or a missing provider, not the screen the setting lives on.\n\n"
            "**Many screens reached from here have their own component.** Passwords "
            "are `Logins`, downloads settings `Downloads`, data choices, studies and "
            "the Nimbus half of Firefox Labs `Experimentation and Telemetry`, and "
            "delete browsing data and the trust panel `Privacy`; each subpackage is "
            "claimed by that component, whose guidance ships alongside, so do not "
            "re-scope a bug that localizes there, just say which component owns the "
            "code. The reverse also happens: "
            "`mobile/android/fenix/app/src/main/java/org/mozilla/fenix/settings/TrackingProtectionFragment.kt` "
            "and "
            "`mobile/android/fenix/app/src/main/java/org/mozilla/fenix/settings/PrivateBrowsingFragment.kt` "
            "sit at this package's root and are owned here though the features are "
            "`Privacy`'s. **Coverage is heavy, so an empty `relevant_tests` is almost "
            "always wrong**: about 100 unit tests under "
            "`mobile/android/fenix/app/src/test/java/org/mozilla/fenix/settings/`, "
            "including `SettingsFragmentTest.kt` at its root, plus "
            "`mobile/android/fenix/app/src/test/java/org/mozilla/fenix/utils/SettingsTest.kt` "
            "for stored values."
        ),
        related=(
            "Firefox for Android :: Logins",
            "Firefox for Android :: Downloads",
            "Firefox for Android :: Experimentation and Telemetry",
            "Firefox for Android :: Privacy",
        ),
    ),
    # Owns the build files of the standalone Fenix, Focus and android-components builds
    # by name, which win over the shared Android trees above by length.
    ScopedComponent(
        "Firefox Build System",
        "Android Studio and Gradle Integration",
        "#android-pie",
        trees=(
            "gradle/",
            "mobile/android/gradle/",
            "mobile/android/shared-settings.gradle",
            "mobile/android/gradle.py",
            "mobile/android/gradle.configure",
            "mobile/android/mach_commands.py",
            "settings.gradle",
            "build.gradle",
        ),
        doc_trees=("mobile/android/docs/",),
        # The build files of each standalone build are named one by one and win over the
        # shared Android trees by length. Not `mobile/android/mach_commands.py`, which also
        # holds `nimbus-cli` and the test commands, nor any component's own `build.gradle`.
        owns=(
            "build.gradle",
            "settings.gradle",
            "gradle.properties",
            "gradlew.bat",
            "substitute-local-geckoview.gradle",
            "gradle/",
            "mobile/android/gradle/",
            "mobile/android/shared-settings.gradle",
            "mobile/android/gradle.py",
            "mobile/android/gradle.configure",
            "mobile/android/focus-android/settings.gradle",
            "mobile/android/focus-android/build.gradle",
            "mobile/android/focus-android/gradle.properties",
            "mobile/android/focus-android/gradle/",
            "mobile/android/fenix/settings.gradle",
            "mobile/android/fenix/build.gradle",
            "mobile/android/fenix/gradle.properties",
            "mobile/android/fenix/gradle/",
            "mobile/android/fenix/plugins/",
            "mobile/android/android-components/settings.gradle",
            "mobile/android/android-components/build.gradle",
            "mobile/android/android-components/gradle.properties",
            "mobile/android/android-components/gradle/",
            "mobile/android/android-components/plugins/",
        ),
        notes=(
            "**There is one Gradle build at the root and three more beside it, and "
            "they get GeckoView differently.** Opening the checkout root in Android "
            "Studio uses `settings.gradle`, which makes `:geckoview`, `:fenix`, "
            "`:focus-android` and android-components projects of one build, filtered "
            "by `MOZ_ANDROID_SUBPROJECT` (`--enable-android-subproject`). "
            "`mobile/android/fenix/settings.gradle`, "
            "`mobile/android/focus-android/settings.gradle` and "
            "`mobile/android/android-components/settings.gradle` are separate builds "
            "with their own wrappers, and CI builds them standalone. They never "
            'include `:geckoview`, so engine-gecko\'s `findProject(":geckoview")` falls '
            'back to a GeckoView AAR from Maven. "My GeckoView change does not show up '
            'in Fenix" therefore depends on which directory was opened, so establish '
            "that first. All four builds apply "
            "`mobile/android/shared-settings.gradle`. Note also that the `idea` block "
            "in root `build.gradle` hides every top-level directory except `gradle`, "
            '`mobile`, `taskcluster` and `widget` from indexing, so "Android Studio '
            'cannot see toolkit/" is intended (bug 2073216), not a defect.\n\n'
            "**Gradle needs a configured objdir before it can configure itself, and "
            "Gradle and mach call each other.** "
            "`mobile/android/gradle/mozconfig.gradle` runs `./mach environment` only "
            "to find the topobjdir and caches the answer, then reads "
            '`config.status.json` from it. "config.status.json not found" or "only '
            'supported for Firefox for Android" means `./mach configure` was never run '
            "for an Android mozconfig, so the fault is the developer's setup, not "
            "Gradle. Under `mach build`, Gradle is invoked through "
            "`mobile/android/gradle.py` with `GRADLE_INVOKED_WITHIN_MACH_BUILD` set. "
            "Under Android Studio or a bare `gradlew`, Gradle runs mach itself: "
            "`machConfigure`, `machBuildFaster` and `machStagePackage` from "
            "`mobile/android/gradle/plugins/conventions/src/main/java/org/mozilla/conventions/MachTasksPlugin.kt`. "
            "`geckoBinariesOnlyIf` in "
            "`mobile/android/gradle/plugins/conventions/src/main/java/org/mozilla/conventions/MachExec.kt` "
            "skips all three inside `mach build`, for official builds and for "
            "multi-locale builds. A redundant rebuild or a stale omnijar from Studio "
            "is therefore those tasks' up-to-date tracking, not AGP.\n\n"
            "**CI resolves dependencies offline, and the moz.build metadata points "
            "elsewhere.** CI sets `GRADLE_USER_HOME` to "
            "`mobile/android/gradle/dotgradle-offline/`, which forces offline mode "
            "against a cache built by `mach android gradle-dependencies` in "
            "`mobile/android/mach_commands.py` and "
            '`taskcluster/scripts/misc/android-gradle-dependencies/`. "Could not '
            'resolve" on CI but not locally is a dependency missing from that cache, '
            "or a conditional one not gated on `DOWNLOAD_ALL_GRADLE_DEPENDENCIES`. "
            "Root `moz.build` and `mobile/android/moz.build` attribute all of these "
            "files to `GeckoView :: General`, so do not re-component on that basis. A "
            "missing SDK or JDK may instead be `python/mozboot/mozboot/android.py`, "
            "which is `Firefox Build System :: Bootstrap Configuration`. Tests are "
            "thin: `build/test/python/test_android_gradle_build.py` (the "
            "`android-gradle-build` subsuite, which covers the builds, mach-task "
            "up-to-date checks and the topobjdir caches), unit tests in "
            "`mobile/android/gradle/plugins/conventions/src/test/`, and apilint's own "
            "tests under `mobile/android/gradle/plugins/apilint/`."
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
    # Password Manager and about:logins are triaged by the same team, and about:logins is a
    # view over Password Manager's storage, so each ships with the other's guidance.
    ScopedComponent(
        "Firefox",
        "PDF Viewer",
        "#pdfjs-triage",
        trees=(
            "toolkit/components/pdfjs/",
            "toolkit/components/aboutpdf/",
            "toolkit/actors/AboutPDFChild.sys.mjs",
            "toolkit/actors/AboutPDFParent.sys.mjs",
        ),
        # The two actors are named one by one: `moz.build` gives them to this component
        # through `Files("AboutPDF*.sys.mjs")`, and the rest of `toolkit/actors/` is
        # `Toolkit :: General`.
        owns=(
            "toolkit/components/pdfjs/",
            "toolkit/components/aboutpdf/",
            "toolkit/actors/AboutPDFChild.sys.mjs",
            "toolkit/actors/AboutPDFParent.sys.mjs",
        ),
        notes=(
            "**Most of this tree is vendored build output, so most viewer bugs are "
            "fixed upstream.** `toolkit/components/pdfjs/content/build/` and "
            "`toolkit/components/pdfjs/content/web/` are pdf.js's `gulp mozcentral` "
            "output, replaced wholesale by `toolkit/components/pdfjs/update.sh` when "
            "`./mach vendor toolkit/components/pdfjs/moz.yaml` runs. Rendering, text "
            "selection, forms, annotations, the editing tools (highlight, comment, "
            "signature, alt text) and the viewer toolbar are fixed in "
            "github.com/mozilla/pdf.js and arrive with the next update; an in-tree "
            "patch to those files is overwritten by it. "
            "`toolkit/components/pdfjs/PdfJsDefaultPrefs.js` is generated the same "
            "way, so a Firefox-specific `pdfjs.*` default goes in "
            "`toolkit/components/pdfjs/PdfJsOverridePrefs.js`, which it `#include`s "
            "and where desktop and Android diverge.\n\n"
            "**The Firefox integration is the `.sys.mjs` beside them and is edited "
            "in-tree**, despite carrying upstream's Apache header. "
            "`toolkit/components/pdfjs/content/PdfStreamConverter.sys.mjs` turns a "
            "response into the viewer (including `application/octet-stream` ones, "
            "under `pdfjs.handleOctetStream`), "
            "`toolkit/components/pdfjs/content/PdfJs.sys.mjs` owns `pdfjs.disabled` "
            "and the handler-service swap, and "
            "`toolkit/components/pdfjs/content/PdfJsParent.sys.mjs` carries the find "
            'bar and the ML alt-text engine. So "downloads instead of opening", "opens '
            'in another app" and "find does nothing" are this layer, while "looks '
            'wrong once open" is upstream. Android loads `viewer-geckoview.html` '
            "through `toolkit/components/pdfjs/content/GeckoViewPdfJsParent.sys.mjs`, "
            "so check the platform first. The print dialog a PDF hands off to is "
            "`toolkit/components/printing/`, which `moz.build` gives to `Toolkit :: "
            "Printing`.\n\n"
            "**`about:pdf` is a desktop-only landing page, not the viewer.** "
            "`toolkit/components/aboutpdf/` is a drop zone and file picker that opens "
            "a PDF in the viewer, a promo that makes Firefox the default PDF app "
            "through `ShellService`, and a `#features` tour. It is registered under "
            "`#ifndef MOZ_WIDGET_ANDROID` in `docshell/base/nsAboutRedirector.cpp`, so "
            "an Android report naming it is mistaken, and a bug in a PDF opened from "
            "it is the viewer's.\n\n"
            "**An empty `relevant_tests` is wrong for integration bugs and usually "
            "right for viewer bugs.** 62 tests in "
            "`toolkit/components/pdfjs/test/browser.toml`, 2 mochitests, 3 xpcshell, "
            "and 6 in `toolkit/components/aboutpdf/tests/browser/`. Upstream's unit "
            "and reference tests are not vendored, so a rendering bug's regression "
            "test belongs upstream; say that rather than reporting no coverage."
        ),
    ),
    ScopedComponent(
        "Toolkit",
        "Password Manager",
        "#credential-management-bug-triage",
        trees=(
            "toolkit/components/passwordmgr/",
            "browser/components/passwordmgr/",
            "toolkit/modules/FormLikeFactory.sys.mjs",
        ),
        # `browser/components/passwordmgr/` is this component and not about:logins despite
        # sitting under `browser/`; `moz.build` gives it to Toolkit.
        owns=(
            "toolkit/components/passwordmgr/",
            "browser/components/passwordmgr/",
            "toolkit/modules/FormLikeFactory.sys.mjs",
        ),
        notes=(
            "**Content detects, the parent decides.** "
            "`toolkit/components/passwordmgr/LoginManagerChild.sys.mjs` finds login "
            "forms in each frame, fills them and notices submission; "
            "`toolkit/components/passwordmgr/LoginManagerParent.sys.mjs` looks logins "
            "up, applies `toolkit/components/passwordmgr/LoginRecipes.sys.mjs` and "
            "hands off to "
            "`toolkit/components/passwordmgr/LoginManagerPrompter.sys.mjs` for the "
            "save/update doorhanger, whose markup is in "
            "`browser/base/content/popup-notifications.inc.xhtml` and whose username "
            'field is `browser/components/passwordmgr/`. "Did not fill" and "did not '
            "offer to save\" are nearly always the child's heuristics, and one site "
            "failing is usually a site recipe (the `password-recipes` Remote Settings "
            "collection) rather than a code change. Sign-up detection and the "
            "generated-password offer hang off the Fathom model in "
            "`toolkit/components/passwordmgr/shared/NewPasswordModel.sys.mjs` scored "
            "against `signon.generation.confidenceThreshold`, so a field that never "
            "gets a suggested password is often just scoring below 0.75.\n\n"
            "**`toolkit/modules/FormLikeFactory.sys.mjs` is shared** with `Toolkit :: "
            "Form Autofill` (addresses and cards) and `Toolkit :: Form Manager` (form "
            "history), so a change there is not local to passwords. The dropdown's "
            "login rows come from "
            "`toolkit/components/passwordmgr/LoginAutoComplete.sys.mjs`, but the popup "
            "and the other rows beside them do not.\n\n"
            "**Two storage backends, chosen by channel.** "
            "`signon.storage.rust.enabled` is true everywhere except release and ESR "
            "in `modules/libpref/init/all.js`, so Nightly and Beta run "
            "`toolkit/components/passwordmgr/storage-rust.sys.mjs` over `logins.db` "
            "while release runs `toolkit/components/passwordmgr/storage-json.sys.mjs` "
            "over `logins.json`. "
            "`toolkit/components/passwordmgr/LoginStorageMigrator.sys.mjs` moves "
            "between them, so logins lost or duplicated after an update on Nightly or "
            "Beta are migration bugs until shown otherwise, and an origin starting "
            "`moz-pwmngr-fixed-` is its dedup rewrite, not corruption. "
            "`signon.storage.rust.active`, not `.enabled`, says which store a profile "
            "is on. Android uses "
            "`toolkit/components/passwordmgr/storage-geckoview.sys.mjs`, which "
            "delegates to the app, so an Android storage bug is usually Fenix.\n\n"
            "**Coverage is heavy, so an empty `relevant_tests` is almost always "
            "wrong.** 72 browser tests, 85 mochitests and 53 xpcshell under "
            "`toolkit/components/passwordmgr/test/`, plus 1 in "
            "`browser/components/passwordmgr/tests/chrome/`. They run on the build's "
            "default backend, so the same file exercises Rust on Nightly and JSON on "
            "release; the switch itself is "
            "`toolkit/components/passwordmgr/test/unit/test_LoginStorageMigrator.js` "
            "and "
            "`toolkit/components/passwordmgr/test/browser/browser_login_storage_migrator.js`."
        ),
        related=("Firefox :: about:logins",),
    ),
    ScopedComponent(
        "Firefox",
        "about:logins",
        "#credential-management-bug-triage",
        trees=("browser/components/aboutlogins/",),
        owns=("browser/components/aboutlogins/",),
        notes=(
            "**The page is a view; the data is Password Manager's.** "
            "`browser/components/aboutlogins/content/` is custom elements in the "
            "privileged-about process that talk only to "
            "`browser/components/aboutlogins/AboutLoginsParent.sys.mjs` over "
            "`AboutLogins:*` messages, and the parent calls straight into "
            "`toolkit/components/passwordmgr/LoginHelper.sys.mjs`, "
            "`toolkit/components/passwordmgr/LoginCSVImport.sys.mjs` and "
            "`toolkit/components/passwordmgr/LoginExport.sys.mjs`. So a CSV import "
            "that mangles rows, an export with the wrong contents, or a login that "
            "vanished or duplicated localizes in `Toolkit :: Password Manager`, whose "
            "guidance ships alongside; this component is the list, filter, sort, "
            'dialogs and edit form. "Import from another browser" opens the migration '
            "wizard through `MigrationUtils` and is not this component either.\n\n"
            "**Breach alerts are split across the two.** The `fxmonitor-breaches` "
            "Remote Settings data is fetched by "
            "`toolkit/components/passwordmgr/BreachAlertsData.sys.mjs`; matching "
            "logins against it is "
            "`browser/components/aboutlogins/LoginBreaches.sys.mjs`, which "
            "`browser/actors/AboutProtectionsParent.sys.mjs` also calls, so a wrong "
            "breach count on about:protections is this file.\n\n"
            "**There is a second passwords UI.** The sidebar's Passwords panel "
            "(`browser.contextual-password-manager.enabled`, true in "
            "`browser/app/profile/firefox.js`) is the megalist in "
            "`toolkit/components/satchel/megalist/`, which `moz.build` gives to "
            "`Toolkit :: Form Manager`. Ask which one the reporter used; "
            "`LoginHelper.openPasswordManager` still opens about:logins.\n\n"
            "**Reauth is the OS only on Windows and macOS.** "
            "`LoginHelper.requestReauth` gates on `canReauth()` in "
            "`toolkit/modules/OSKeyStore.sys.mjs`, false on Linux, and with no primary "
            "password and OS auth off or unsupported it authorizes without any prompt "
            "-- so revealing a password unprompted is expected there. "
            "`signon.management.page.os-auth.locked.enabled` is kept locked, so "
            "about:config shows its `false` default rather than the value "
            "`LoginHelper.getOSAuthEnabled` unlocks and reads. Coverage is good, so an "
            "empty `relevant_tests` is usually wrong: 29 browser, 6 chrome and 1 "
            "xpcshell under `browser/components/aboutlogins/tests/`, with the OS-auth "
            "paths mocked through `OSKeyStore` rather than the real dialog."
        ),
        related=("Toolkit :: Password Manager",),
    ),
    # The three buckets, which are where a filing lands when the reporter could not pick
    # a component. Grouped at the end rather than beside a related area because they are
    # not an area: they route to one channel for the people who work the unowned queue.
    #
    # None of the three sets `owns`, which is deliberate and is the one thing to preserve
    # if these entries are edited. `owns` is what `hooks.component_guidance_hook` refuses
    # comments against and `owners_for_path` resolves longest-claim-wins, so claiming
    # `browser/` for General would make every unclaimed file of desktop chrome General's
    # and a Sidebar or Settings UI run -- which loads its own guidance and its `related`
    # entries, not this one -- would have its comment refused for citing its own code. A
    # `trees` this broad is only legal because nothing owns it.
    ScopedComponent(
        "Firefox",
        "General",
        "#fx-toolkit-general-triage-notifications",
        trees=("browser/",),
        notes=(
            "**A holding component rather than an area, so the first useful output is "
            "which component the bug belongs to**, not which file is at fault. The tree "
            "above is the whole of desktop chrome and most of it belongs to some other "
            "component, so a confident localization here is a re-componentization "
            "suggestion: name the component that owns the code you found and say so "
            "plainly, because the reporter picked this one for want of a better guess "
            "rather than as a claim about where the code is. What legitimately stays "
            "here is cross-component window, session and startup behavior that no single "
            "team owns."
        ),
    ),
    ScopedComponent(
        "Toolkit",
        "General",
        "#fx-toolkit-general-triage-notifications",
        trees=("toolkit/",),
        notes=(
            "The same holding-component caveat as `Firefox :: General`, with one "
            "difference worth acting on: toolkit code is shared, so a bug filed here may "
            "reproduce in Thunderbird and the other consumers as well as Firefox, and "
            "the component that owns the code is as likely to be a `Core` one as a "
            "`Toolkit` one. Establish which application the reporter was running before "
            "localizing anything, since the same symptom in two consumers is usually two "
            "different bugs."
        ),
    ),
    ScopedComponent(
        "Firefox",
        "Untriaged",
        "#fx-toolkit-general-triage-notifications",
        trees=("browser/",),
        notes=(
            "**Not a component at all**: it is the default for a filing that named none, "
            "so it says nothing about the area and everything on `Firefox :: General` "
            "applies to it more strongly. One thing it adds is a race worth writing "
            "around. bugbot's `component` rule moves low-confidence bugs out of here "
            "into `Firefox :: General` hourly, and it runs in the same cron pass as the "
            "rule that sends bugs here, so a bug may be reassigned between the run "
            "starting and anyone reading the comment. Both components report to this "
            "channel, so nothing is lost, but do not write a comment whose reasoning "
            "depends on the bug still being Untriaged."
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
    homepage claims `…/fenix/home/` and every Android component claims
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
