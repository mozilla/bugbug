"""Static configuration: scope, accounts, allowed writes and the comment limits.

Everything a hook, the preflight or the prompt needs to agree on lives here, so a
rule stated in `rules/` has one place in code that enforces it.
"""

import os
from pathlib import Path

from .teamconfig import (
    EMAIL,
    OTHER_ISSUE_TYPE,
    load_people,
    load_team,
    load_terms,
    meta_bug_ids,
    team_dir,
)

# The agent's name in hackbot-api. Hackbot tags every comment it posts for this agent
# with it (`comment_tags = [ctx.agent]`), which tells our comments from other agents'.
AGENT_TAG = "gecko-triage"

# The account the apply step posts as, for every Hackbot agent. A newest comment from
# it with `AGENT_TAG`, with no change after it, skips the bug. The agent never sends it
# a needinfo.
BOT_ACCOUNT = "hackbot@mozilla.tld"

# Bugzilla MCP tool names as exposed to the agent (mcp__<server>__<tool>).
# No `download_attachment`: it writes inside the broker container, which no agent
# here can read.
BUGZILLA_READ_TOOLS = [
    "mcp__bugzilla__search_bugs",
    "mcp__bugzilla__get_bugs",
    "mcp__bugzilla__get_bug_comments",
    "mcp__bugzilla__get_bug_attachments",
]

SEARCHFOX_TOOLS = [
    "mcp__searchfox__search_identifier",
    "mcp__searchfox__search_text",
    "mcp__searchfox__find_definition",
    "mcp__searchfox__get_function_at_line",
    "mcp__searchfox__get_blame",
    "mcp__searchfox__get_file",
]

MOZILLA_VCS_TOOLS = [
    "mcp__mozilla_vcs__get_commit_info",
    "mcp__mozilla_vcs__get_commit_diff",
    "mcp__mozilla_vcs__file_history",
]

# Recordable action types. `update_bug` carries the fields in `ALLOWED_UPDATE_FIELDS`;
# `hooks.py` checks each one and refuses every other field.
ENABLED_ACTION_TYPES = [
    "bugzilla.add_comment",
    "bugzilla.update_bug",
]


# The team this run triages for: the optional `team` run input, which hackbot-api
# passes as `TEAM`. It picks `teams/<team>/`, with `team.toml` and the team's `rules/`.
# With no team, every team value below is empty and the run uses the shared rules only,
# in the bug's own component. An unknown team name fails the import.
TEAMS_DIR = Path(__file__).parent / "teams"
TEAM_NAME = os.environ.get("TEAM")
TEAM_DIR = team_dir(TEAMS_DIR, TEAM_NAME) if TEAM_NAME else None
TEAM = load_team(TEAM_DIR / "team.toml") if TEAM_DIR else None
TEAM_RULES_DIR = TEAM_DIR / "rules" if TEAM_DIR else None
# The capture block and its checks; None with no team.
CAPTURE = TEAM.comment if TEAM else None
SHARED_DIR = Path(__file__).parent / "shared"
TERMS = load_terms(SHARED_DIR / "bugzilla.toml")
PEOPLE = load_people(SHARED_DIR / "people.toml")


def parse_bot_aliases(text: str) -> frozenset[str]:
    """`BOT_ALIASES`: comma-separated logins that count as the agent by author alone.

    They count with or without the tag. For a pilot where another account posts the results. The skip check and the
    needinfo check read them. They stay out of the repo, because the account is
    temporary.
    """
    aliases = frozenset(a.strip().lower() for a in text.split(",") if a.strip())
    bad = sorted(a for a in aliases if not EMAIL.fullmatch(a))
    if bad:
        raise RuntimeError(f"BOT_ALIASES holds a value that is not an email: {bad}")
    return aliases


BOT_ALIASES = parse_bot_aliases(os.environ.get("BOT_ALIASES", ""))

# With no team, `teamconfig.scope_for` gives the bug's own component instead.
TRIAGE_SCOPE = TEAM.components if TEAM else ()

SCOPE_KEYS = frozenset(entry.key for entry in TRIAGE_SCOPE)

# The developers the agent may needinfo without a move, by area.
NEEDINFO_EXPERTS = (
    frozenset(e.email for e in TEAM.needinfo_experts) if TEAM else frozenset()
)

# Who to ask when no expert's area matches; one run may ask only one of them.
NEEDINFO_FALLBACK = frozenset(TEAM.needinfo_fallback) if TEAM else frozenset()

# The values the plan's `issue_type` may take: the team's list and `other`. Empty with no
# team or no list, and then the plan carries no `issue_type`.
ISSUE_TYPES = (
    frozenset({*(t.value for t in TEAM.issue_types), OTHER_ISSUE_TYPE})
    if TEAM and TEAM.issue_types
    else frozenset()
)

# The most needinfo requests one run may record.
MAX_NEEDINFOS = 3

# The most meta bugs one run may add to the triaged bug's `blocks`.
MAX_META_BUGS = 2

# A needinfo is stale after this long with no answer, whoever set it.
STALE_NEEDINFO_DAYS = 14

# Severity and priority only when the current value is `--`, and never S1. S1 forces P1
# and nominates release tracking on BMO, which is a human's call. P4 is reserved for web
# platform test bots and P5 means "will not fix", a person's decision.
UNSET_FIELD_VALUE = "--"
TRIAGE_SEVERITIES = frozenset({"S2", "S3", "S4"})
TRIAGE_PRIORITIES = frozenset({"P1", "P2", "P3"})

# The resolutions the agent may record. WORKSFORME is never one: a bug that stops
# reproducing is not fixed. INCOMPLETE also needs every open needinfo on the reporter.
TRIAGE_RESOLUTIONS = frozenset(
    {"INVALID", "WONTFIX", "FIXED", "DUPLICATE", "INCOMPLETE"}
)

# The meta bugs a bug may be made to block: the team's `meta-bugs.md` table.
TEAM_META_BUGS = (
    meta_bug_ids((TEAM_RULES_DIR / "meta-bugs.md").read_text())
    if TEAM_RULES_DIR
    else frozenset()
)

# Fields `bugzilla.update_bug` may carry. Anything else is refused by name.
RESOLUTION_FIELDS = frozenset({"status", "resolution", "dupe_of"})
MOVE_FIELDS = frozenset({"product", "component"})
ALLOWED_UPDATE_FIELDS = (
    RESOLUTION_FIELDS
    | MOVE_FIELDS
    | {
        "severity",
        "priority",
        "flags",
        "blocks",
        "keywords",
        "regressed_by",
        "see_also",
        "whiteboard",
    }
)
# Bugzilla refuses a move to another product unless the version and milestone exist
# there; these two exist in every product.
CROSS_PRODUCT_DEFAULTS = {"version": "unspecified", "target_milestone": "---"}

# The hosts a comment may link to. `*.` also allows the domain itself. The permalink
# placeholder expands to searchfox.org. The capture block's video link is not checked.
ALLOWED_LINK_HOSTS = (
    "*.mozilla.org",
    "*.mozilla.net",
    "phabricator.services.mozilla.com",
    "mozilla.github.io",
    "searchfox.org",
    "profiler.firefox.com",
    "share.firefox.dev",
    "*.w3.org",
    "*.whatwg.org",
    "w3c.github.io",
    "wicg.github.io",
    "webaudio.github.io",
    "aomediacodec.github.io",
    "github.com",
    "tc39.es",
    "*.rfc-editor.org",
    "datatracker.ietf.org",
    "www.itu.int",
    "wpt.fyi",
    "issues.chromium.org",
    "bugs.chromium.org",
    "crbug.com",
    "*.webkit.org",
)

# ASD-STE100 limits for descriptive writing, checked on every comment.
MAX_WORDS_PER_SENTENCE = 25
MAX_SENTENCES_PER_PARAGRAPH = 6

# The team file's capture block and this mozregression block are quoted verbatim. The
# model writes a placeholder and a hook expands it, so the steps stay exact; the comment
# limits do not count them.
CAPTURE_PLACEHOLDER = "{{gecko_triage.capture_block}}"
MOZREGRESSION_PLACEHOLDER = "{{gecko_triage.mozregression_block}}"
MOZREGRESSION_BLOCK = """Please find the regression range with mozregression:

1. Install it from https://mozilla.github.io/mozregression/.
2. Run it with the first known bad and the last known good versions.
3. Follow the steps that show the problem on each build it opens.
4. Paste the final "pushlog" link into this bug."""
