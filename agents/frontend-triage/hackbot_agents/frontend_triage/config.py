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


# Recordable action types the agent may take, by dotted id. This agent triages
# and plans only: it records a comment with its findings/plan and, at high
# confidence, may propose field updates (e.g. keyword/severity). It never
# creates bugs or attaches files.
#
# `bugzilla.update_bug` needs `editbugs` on the apply account. The apply step coalesces
# a same-bug field change with the nearest comment into one PUT, so losing that
# privilege would take the analysis comment down with the rejected field change.
ENABLED_ACTION_TYPES = [
    "bugzilla.add_comment",
    "bugzilla.update_bug",
]


class ScopedComponent(NamedTuple):
    """A Bugzilla component sent here for triage, and where a finished run reports it."""

    product: str
    component: str
    # Which `Source repository` bullet in prompts/system.md describes this component's
    # code. `tests/test_plan.py` asserts every area named here has one, so a new area
    # cannot be added without the guidance that makes it triageable.
    area: str
    # Required, because an entry without one would be a component getting unattended
    # triage with nobody told -- which is what `channel_for` failing closed produces,
    # and not something to be able to express by accident.
    channel: str

    @property
    def key(self) -> str:
        return f"{self.product} :: {self.component}"


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
    ScopedComponent("Firefox", "Sharing", "Sharing", "#content-sharing-automation"),
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

# What a `bugzilla.update_bug` from this agent may touch. Enforced at record time
# by `hooks.update_bug_hook`, so an out-of-bounds change is refused while the agent
# can still correct it, rather than recorded and held for a human later.

TRIAGE_FIELDS = frozenset({"keywords", "severity"})

# Bugzilla's `bug_severity` legal values are `--`, `blocker`, `S1`, `critical`,
# `S2`, `major`, `normal`, `S3`, `minor`, `S4`, `trivial`, `N/A`, `enhancement`
# (https://bugzilla.mozilla.org/rest/field/bug/bug_severity). Narrowed to the four
# `rules/severity-assessment.md` actually defines: the word forms are legacy, kept
# for old bugs, and `--`/`N/A` mean unset or not-applicable, which is a metadata
# regression rather than a triage judgment.
TRIAGE_SEVERITIES = frozenset({"S1", "S2", "S3", "S4"})

# Bugzilla defines ~340 keywords (https://bugzilla.mozilla.org/rest/field/bug/keywords,
# or https://bugzilla.mozilla.org/describekeywords.cgi for the annotated list), several
# of which drive automation. These six are the ones a frontend triage pass can add
# without side effects. No ruleset in `rules/` directs a keyword addition today, so
# widen this set alongside the rule that needs it rather than ahead of one.
TRIAGE_KEYWORDS = frozenset(
    {
        "access",
        "dataloss",
        "good-first-bug",
        "papercut",
        "perf",
        "regression",
    }
)
