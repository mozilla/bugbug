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


# Per-area source-tree guidance (in-process MCP server "areas"). Only reached when the
# agent localizes outside the area its component maps to; the usual case is already in
# the prompt.
AREA_TOOLS = [
    "mcp__areas__load_area_guidance",
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
    # Which `rules/areas/` file describes this component's code. `tests/test_plan.py`
    # asserts every area named here has one, so a new area cannot be added without the
    # guidance that makes it triageable.
    area: str
    # Required, because an entry without one would be a component getting unattended
    # triage with nobody told -- which is what `channel_for` failing closed produces,
    # and not something to be able to express by accident.
    channel: str
    # Areas whose guidance goes in the prompt alongside `area`, for components that
    # routinely turn out to be somewhere else. Sharing is the known one: a "stop
    # sharing" report arrives here but is WebRTC, which site permissions owns. Listing
    # the pair means both files are present from the start, rather than the agent
    # having to notice mid-run and fetch the second one.
    related_areas: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        return f"{self.product} :: {self.component}"


class Area(NamedTuple):
    """One `rules/areas/` guidance file, and the trees whose code it describes."""

    name: str
    slug: str
    # Where this area's code lives, for the prompt's index. Descriptive, and allowed to
    # be broad and to overlap another area: `browser/` names the desktop frontend
    # usefully even though most other areas sit inside it.
    trees: tuple[str, ...]
    # Paths this area **exclusively** owns, for `area_for_path` and so for
    # `hooks.area_guidance_hook`. Narrower than `trees` on purpose -- enforcement needs
    # "no other area could mean this file", and `browser/` fails that badly enough to
    # refuse comments the guidance itself asked for: `rules/areas/ip-protection.md`
    # sends the agent to `browser/app/profile/firefox.js` for prefs.
    #
    # Empty for the desktop frontend, which is the general case and owns nothing
    # exclusively. It costs the least to leave unenforced -- its guidance is two lines,
    # against the installer's NSIS or Android's Kotlin.
    owns: tuple[str, ...] = ()


# Every area, in the order they are listed to the model. `slug` is the filename under
# `rules/areas/`; `trees` drives both that index and `hooks.area_guidance_hook`.
AREAS = (
    # No `owns`: everything below sits inside these trees.
    Area("Desktop frontend", "desktop-frontend", ("browser/", "toolkit/", "devtools/")),
    Area(
        "Site permissions",
        "site-permissions",
        (
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
    ),
    Area(
        "Sharing",
        "sharing",
        (
            "browser/modules/SharingUtils.sys.mjs",
            "browser/components/contentsharing/",
            "widget/ (the per-OS half)",
        ),
        # Not `widget/`: that is the whole platform widget layer, and a bug in any
        # other area citing a file there has nothing to do with sharing a URL out.
        owns=(
            "browser/modules/SharingUtils.sys.mjs",
            "browser/components/contentsharing/",
            "widget/nsIMacSharingService.idl",
            "widget/cocoa/nsMacSharingService.mm",
        ),
    ),
    Area(
        "IP Protection",
        "ip-protection",
        ("browser/components/ipprotection/", "toolkit/components/ipprotection/"),
        owns=("browser/components/ipprotection/", "toolkit/components/ipprotection/"),
    ),
    Area(
        "Messaging System",
        "messaging-system",
        (
            "browser/components/asrouter/",
            "browser/components/aboutwelcome/",
            "toolkit/components/messaging-system/",
        ),
        owns=(
            "browser/components/asrouter/",
            "browser/components/aboutwelcome/",
            "toolkit/components/messaging-system/",
        ),
    ),
    Area(
        "Firefox for Android",
        "firefox-for-android",
        ("mobile/android/",),
        owns=("mobile/android/",),
    ),
    Area(
        "Application updater",
        "application-updater",
        ("toolkit/mozapps/update/",),
        owns=("toolkit/mozapps/update/",),
    ),
    Area(
        "Windows installer",
        "windows-installer",
        ("browser/installer/",),
        owns=("browser/installer/",),
    ),
)

AREAS_BY_NAME = {a.name: a for a in AREAS}


# The components that are sent here for triage, and the channel that owns each. The
# single source of truth for both: `SLACK_CHANNELS` below is derived from it, and
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
# the agent is never given the tool — it has no say in what is said or where.
#
# Ordered by area, grouped by first appearance — `render_scope` preserves that order, so
# this is also the order the model reads. There is no separate list of areas to keep in
# sync with this one.
TRIAGE_SCOPE = (
    ScopedComponent("Firefox", "New Tab Page", "Desktop frontend", "#hnt-dev-triage"),
    ScopedComponent(
        "Firefox", "Site Permissions", "Site permissions", "#privacy-team-automation"
    ),
    ScopedComponent(
        "Firefox",
        "Sharing",
        "Sharing",
        "#content-sharing-automation",
        related_areas=("Site permissions",),
    ),
    ScopedComponent(
        "Firefox",
        "IP Protection",
        "IP Protection",
        "#team-eng-ip-protection-triage",
    ),
    ScopedComponent("Firefox", "Messaging System", "Messaging System", "#omc-triage"),
    ScopedComponent(
        "Firefox for Android", "History", "Firefox for Android", "#android-core-dev"
    ),
    ScopedComponent(
        "Firefox for Android", "Toolbar", "Firefox for Android", "#android-core-dev"
    ),
    ScopedComponent(
        "Firefox for Android", "Homepage", "Firefox for Android", "#android-core-dev"
    ),
    # The installer and the updater are triaged by the same team, so two components
    # share a channel. Keying by product-and-component rather than by channel is what
    # lets them, without either one having to know about the other.
    ScopedComponent(
        "Toolkit",
        "Application Update",
        "Application updater",
        "#installer-updater-bug-triage",
    ),
    ScopedComponent(
        "Firefox", "Installer", "Windows installer", "#installer-updater-bug-triage"
    ),
)

# Where an auto-applied run reports itself, by `"<Product> :: <Component>"`. Derived, so
# that `notify.py` keeps one flat mapping to look up.
SLACK_CHANNELS = {c.key: c.channel for c in TRIAGE_SCOPE}

_SCOPE_BY_KEY = {c.key: c for c in TRIAGE_SCOPE}


def areas_for(product: str | None, component: str | None) -> tuple[Area, ...]:
    """The areas whose guidance belongs in the prompt for a bug in this component.

    **Every** area when the component is not one we triage, or when the caller could
    not determine it. `rules/scoping.md` is explicit that a defect in an unlisted
    component is still in scope, and a run that guessed one area for such a bug would
    have less to work with than it does today. Failing open costs the current prompt
    size and nothing else, so it is the only safe default.
    """
    entry = _SCOPE_BY_KEY.get(
        f"{(product or '').strip()} :: {(component or '').strip()}"
    )
    if entry is None:
        return AREAS
    return tuple(AREAS_BY_NAME[name] for name in (entry.area, *entry.related_areas))


def area_for_path(path: str) -> Area | None:
    """The area that exclusively owns ``path``, or None if none does.

    None is the common and correct answer, not a failure. It covers a file outside the
    triaged areas (`gfx/`) and any ordinary desktop chrome file (`browser/base/...`),
    which no area owns exclusively -- see `Area.owns`. Callers must read it as "no
    guidance is specific to this file", never as "guidance is missing".

    Longest match wins, so `browser/installer/...` is the installer even though the
    desktop frontend describes `browser/`.
    """
    best: tuple[int, Area] | None = None
    for area in AREAS:
        for owned in area.owns:
            if path.startswith(owned) and (best is None or len(owned) > best[0]):
                best = (len(owned), area)
    return best[1] if best else None


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
