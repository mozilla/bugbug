"""One regression guard for the memory the agent no longer keeps on a bug.

The agent used to find its own comments by their tag, end each comment with a footer and
read that footer back. A dry run could replay another account's history. All of it is
gone, and none of it may come back.
"""

import importlib.util
import inspect
from pathlib import Path

from hackbot_agents.gecko_triage import agent, hooks
from hackbot_agents.gecko_triage.__main__ import AgentInputs
from hackbot_agents.gecko_triage.state import TriageState, fetch_state

PACKAGE = Path(__file__).parents[1] / "hackbot_agents" / "gecko_triage"

REMOVED = {
    "state": (
        TriageState(bug={}),
        (
            "_ours",
            "our_comments",
            "been_here",
            "last_comment",
            "last_comment_time",
            "last_footer",
            "anything_new",
            "waiting_on",
            "stale_needinfos",
            "comments_by_author",
            "bot_account",
            "reporter_commented_after",
        ),
    ),
    "hooks": (hooks, ("footer_hook", "footer_module", "_body", "_check_capture_block")),
    "agent": (agent, ("footer_module", "capture_block_refusal")),
}

# Model-facing phrases of the old design.
REMOVED_TEXT = (
    "footer",
    "returning bug",
    "has been here",
    "what it asked last time",
    "when the agent has commented before",
    "agent's last comment",
    "you come back to a bug",
    "first time the agent looks at this bug",
)


def test_the_removed_memory_stays_removed():
    for where, (owner, names) in REMOVED.items():
        for name in names:
            assert not hasattr(owner, name), f"{where}.{name}"
    assert importlib.util.find_spec("hackbot_agents.gecko_triage.footer") is None
    params = inspect.signature(fetch_state).parameters
    assert "bot_account" not in params and "comments_by_author" not in params
    assert "bot_account" not in AgentInputs.model_fields

    texts = {
        str(path.relative_to(PACKAGE)): " ".join(path.read_text().lower().split())
        for path in (
            PACKAGE / "prompts" / "system.md",
            *(PACKAGE / "rules").glob("*.md"),
            *(PACKAGE / "teams").glob("*/rules/*.md"),
        )
    }
    assert any(name.startswith("teams/") for name in texts)
    for name, text in texts.items():
        for phrase in REMOVED_TEXT:
            assert phrase not in text, f"{phrase!r} in {name}"


# (file under the package, phrase): old rule wording that must not come back.
REMOVED_RULE_TEXT = (
    ("rules/workflow.md", "Triaged."),
    ("rules/workflow.md", "## Triaged"),
    ("rules/workflow.md", "step 2c"),
    ("rules/workflow.md", "Step 2c"),
    ("rules/workflow.md", "**4a.**"),
    ("rules/workflow.md", "the tools refuse"),
    ("rules/workflow.md", "not changed when asking for information"),
    ("rules/workflow.md", "only together with a move to their component"),
    ("rules/workflow.md", "otherwise needinfo nobody"),
    ("rules/workflow.md", "P/S standard"),
    ("rules/workflow.md", ":nick"),
    ("rules/workflow.md", "say only that a recent change caused the regression"),
    ("rules/workflow.md", "Set `regressed_by` also when"),
    ("rules/workflow.md", "Searchfox source at the run's revision"),
    ("rules/workflow.md", "makes this close an INVALID close"),
    ("rules/workflow.md", "If not, name them in the comment."),
    ("rules/workflow.md", "your analysis names the change"),
    ("rules/workflow.md", "or you find the change that caused it"),
    ("rules/workflow.md", "confirmed regressor"),
    ("rules/workflow.md", "This case also needs the proof above."),
    ("rules/workflow.md", "is a bot's regression note"),
    ("prompts/system.md", "triaged"),
    ("prompts/system.md", "close / move"),
    ("prompts/system.md", "only when the regressor is known"),
    ("rules/diagnostics.md", "only when `fetch_attachment`"),
    ("rules/diagnostics.md", "read the log of every failing platform"),
    ("rules/diagnostics.md", "a failure can stop"),
    ("rules/severity-priority.md", "say in the comment why a human should consider"),
    ("rules/severity-priority.md", "not crash frequency"),
    ("rules/severity-priority.md", "Complete loss of core functionality; crash"),
    ("rules/severity-priority.md", "Crash with a crash-stats id, multiple reporters"),
    ("teams/media/rules/meta-bugs.md", "when it fits"),
    ("rules/comment.md", '"possible"'),
    ("rules/workflow.md", "Crash bots such as"),
    ("rules/workflow.md", "there is nothing for the agent to wait for"),
    ("rules/diagnostics.md", "say in the comment that the newest runs pass"),
    ("rules/comment.md", "a known regression, not known"),
    ("rules/comment.md", "When no log is attached, ask"),
    ("prompts/log_analyst.md", "download it with `curl`"),
)


def test_the_old_rule_wording_stays_removed():
    for name, phrase in REMOVED_RULE_TEXT:
        text = " ".join((PACKAGE / name).read_text().split())
        assert phrase not in text, f"{phrase!r} in {name}"


OLD_SECTION_MARK = chr(0xA7) + "1"


def test_no_file_in_the_agent_uses_the_old_section_labels():
    root = PACKAGE.parents[1]
    seen = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        seen += 1
        assert OLD_SECTION_MARK not in text, f"{path} says {OLD_SECTION_MARK}"
    assert seen > 10
