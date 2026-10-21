"""Models and tool allowlist for the perftest-repair agent."""

MODEL = "claude-opus-5"
EFFORT = "high"

# Most perf test intermittents are fixed in harness or test scripts, and many fail
# only on Android, Mac or Windows hardware, so a Linux build verifies little for its
# cost. A run can still ask for one.
SKIP_FIREFOX_BUILD = True

# Tools that can modify the source repo.
SOURCE_WRITE_TOOLS = ["Write", "Edit", "MultiEdit"]

# Bugzilla MCP tool names as exposed to the agent (mcp__<server>__<tool>).
BUGZILLA_READ_TOOLS = [
    "mcp__bugzilla__search_bugs",
    "mcp__bugzilla__get_bugs",
    "mcp__bugzilla__get_bug_comments",
    "mcp__bugzilla__get_bug_attachments",
    "mcp__bugzilla__download_attachment",
]

# Every run's report goes here, recorded by code once the run has a result.
SLACK_CHANNEL = "#perftest-intermittent-hackbot"

# The one action the agent records itself: a try push of its suggested fix.
ACTIONS = ["try_server.push"]

# How many times the try push runs the failing job. A permafail fails every run,
# so a few green runs show the fix; an intermittent needs more.
TRY_REBUILD_INTERMITTENT = 10
TRY_REBUILD_PERMAFAIL = 3

# The `ref` the try push is recorded under, so the Slack report can link it as
# `{{actions.try.url}}` once hackbot-api has applied the push.
TRY_REF = "try"

# hackbot-api opens the fix for review if at most this share of try runs failed.
# The try results are posted in the thread of the Slack message under SLACK_REF.
MAX_TRY_FAILURE_RATE = 0.10
REVISION_REVIEWERS = ["#perftest-reviewers"]
SLACK_REF = "announce"

# Only the build tool: evaluate_testcase / evaluate_js_shell do not run the perf
# harnesses, so the agent runs the test itself with mach over Bash.
FIREFOX_TOOLS = ["mcp__firefox__build_firefox"]

ALLOWED_TOOLS = [
    "Read",
    "Grep",
    "Glob",
    "Bash",
    "Task",
    "WebFetch",
]

ADDITIONAL_DIRS = [
    "~/.mozbuild",
    "~/.cache/uv/",
]
