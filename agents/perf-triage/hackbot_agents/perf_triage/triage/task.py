"""The triage task (step 1): rate a bug's performance impact.

Consumes the `triage-perf-bug` skill rather than reimplementing it, for the
reason given in ``prompt.py``: the judgement is maintained upstream and measured
by perf-triage-validator's corpus, so a local copy would drift out of agreement
with the thing being validated.
"""

import os
from collections.abc import Sequence
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, McpServerConfig
from hackbot_agents.perf_triage.config import BUGZILLA_READ_TOOLS
from hackbot_agents.perf_triage.inputs import BugDataInput, PerfInput
from hackbot_agents.perf_triage.plugin import PROFILER_ANALYSIS_SKILL, perf_plugin
from hackbot_agents.perf_triage.tasks import RunTracker, Task, TaskConfig
from hackbot_agents.perf_triage.triage.prompt import SKILL_NAME, TRIAGE_TASK_DETAILS
from hackbot_agents.perf_triage.triage.result import TriageResult
from perf_triage_calculator import perf_triage as calc

# Where the skill finds the calculator script. It resolves $PERF_TRIAGE_PATH
# first and otherwise looks in its own repository root, which does not exist
# here, so the task always sets it: to the env value when one is given (to test
# an unreleased calculator), else to the installed package's module.
PERF_TRIAGE_PATH_ENV = "PERF_TRIAGE_PATH"


def calculator_path() -> str:
    return os.environ.get(PERF_TRIAGE_PATH_ENV) or calc.__file__


# Agent mode switches the skill's Step 5 to a single JSON object (and its Step 3
# is governed by PERF_TRIAGE_SKIP_PROFILE below). WITHOUT IT the skill runs interactive mode: it walks the profile a
# second time (paying step 4's price for a shallower answer) and returns
# paste-ready Markdown wrapped in four-backtick fences with `### Comment N`
# markers, which is not something this task can turn into a TriageResult. That
# failure is quiet -- a plausible-looking run that costs more and yields less --
# so the task sets the variable itself rather than trusting the environment.
PERF_TRIAGE_AGENT_MODE_ENV = "PERF_TRIAGE_AGENT_MODE"

# Agent mode runs the skill's profile step unless this is set. A caller that
# analyses the profile itself downstream sets it, rather than pay twice. perf-triage
# leaves it unset when it found a profile, because the profile can change the rating.
PERF_TRIAGE_SKIP_PROFILE_ENV = "PERF_TRIAGE_SKIP_PROFILE"


class PerfTriage(Task):
    """Rates one bug with the Performance Impact Calculator.

    Runs FIRST, before classification, because the rating needs no profile and
    no measurement -- it is the cheapest useful output in the flow, and on a bug
    that turns out to be out of scope it is the only one.
    """

    name = "perf_triage"
    result_cls = TriageResult

    def __init__(
        self,
        task_config: TaskConfig,
        run_tracker: RunTracker,
        input_data: PerfInput,
        bugzilla_mcp_server: McpServerConfig,
        *,
        profile_urls: Sequence[str] = (),
        analyse_profile: bool = False,
    ):
        super().__init__(task_config, run_tracker)
        self.input_data = input_data
        self.profile_urls = list(profile_urls)
        self.analyse_profile = analyse_profile
        # The skill's own Step 1 shells out to `bmo-to-md` to fetch the bug. We
        # do not install it: the broker already holds the Bugzilla credential
        # and the MCP tools go through it, so the agent container carries no
        # credential of its own. Same substitution every other task makes.
        if self.input_data.type == "bug_id":
            self.add_mcp_server("bugzilla", bugzilla_mcp_server, BUGZILLA_READ_TOOLS)

    def subject(self) -> Any:
        return self.input_data.subject()

    def system_prompt(self) -> str:
        return super().system_prompt().format(task_details=TRIAGE_TASK_DETAILS)

    def agent_options(self) -> ClaudeAgentOptions:
        options = super().agent_options()
        # Set agent mode for the subprocess the SDK spawns. Done here rather
        # than left to compose.yml because a missing entry there would be
        # invisible: the skill would quietly run interactive mode and this task
        # would fail to parse its Markdown, after paying for a profile walk it
        # was told to skip. Belt to compose's braces.
        env = {
            **(options.env or {}),
            PERF_TRIAGE_AGENT_MODE_ENV: "1",
            PERF_TRIAGE_PATH_ENV: calculator_path(),
        }
        if not self.analyse_profile:
            env[PERF_TRIAGE_SKIP_PROFILE_ENV] = "1"
        options.env = env
        # The skills come from the `perf` plugin. The SDK sets setting_sources
        # itself and adds the Skill tool when `skills` is given, so clear it
        # rather than pinning another list.
        options.plugins = perf_plugin()
        options.skills = (
            [SKILL_NAME, PROFILER_ANALYSIS_SKILL]
            if self.analyse_profile
            else [SKILL_NAME]
        )
        options.setting_sources = None
        return options

    def user_prompt(self) -> str:
        lines: list[str] = []
        if isinstance(self.input_data, BugDataInput):
            lines += [
                "Triage this performance report:",
                "",
                self.input_data.bug_data,
            ]
        else:
            lines.append(
                f"Triage Bugzilla bug {self.input_data.bug_id}. Fetch it with the "
                "Bugzilla MCP tools rather than bmo-to-md, which is not installed "
                "here."
            )
        # Tell the model where the calculator is. The skill would otherwise fall
        # back to its own repository root, which does not exist in a container.
        path = calculator_path()
        lines += [
            "",
            f"The calculator is at {path} (also in ${PERF_TRIAGE_PATH_ENV}). "
            "Run it from there; do not search for it.",
        ]
        if self.analyse_profile and self.profile_urls:
            lines += ["", "Profiles linked from this bug (analyse them in Step 3):"]
            lines += [f"- {u}" for u in self.profile_urls]
        lines += ["", "Follow your task procedure."]
        return "\n".join(lines)
