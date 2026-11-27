"""Firefox ETP web-compatibility agent.

Drives an agent that checks whether a broken-site report is caused by
Enhanced Tracking Protection's Strict mode and, if a classifier list blocks a
resource the site needs, proposes entries for the `url-classifier-exceptions`
Remote Settings collection. The bug is passed either inline as ``bug_data``
text or a Bugzilla ``bug_id`` (read via Bugzilla broker).
"""

import logging
import re
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, get_args

import requests
from autowebcompat_tools.browser import install_firefox
from autowebcompat_tools.environment import Environment
from autowebcompat_tools.inputs import AutoWebcompatInput, BugDataInput, BugIdInput
from autowebcompat_tools.task import RunTracker, Task, TaskConfig
from claude_agent_sdk import ClaudeAgentOptions, McpServerConfig

from .config import BUGZILLA_READ_TOOLS, DEVTOOLS_TOOLS, PRIVILEGED_TOOLS
from .exceptions import (
    EXCEPTIONS_SERVER_NAME,
    FIND_EXCEPTIONS_TOOL,
    build_exceptions_server,
    find_existing_exceptions,
    pattern_host,
)
from .firefox_build import firefox_build
from .mcp_servers import build_firefox_devtools_server
from .preflight import preflight
from .result import (
    RESULT_SERVER_NAME,
    ClassifierFeature,
    EtpFindings,
    EtpInvestigationResult,
    ProposedShim,
)
from .shims import (
    FIND_SHIMS_TOOL,
    SHIMS_SERVER_NAME,
    SHIMS_URL,
    build_shims_server,
    matching_shims,
    parse_shims,
)
from .tracker_lists import (
    CHECK_TRACKER_LISTS_TOOL,
    TRACKER_LISTS_SERVER_NAME,
    build_tracker_lists_server,
)

HERE = Path(__file__).resolve().parent

logger = logging.getLogger("autowebcompat-etp")

# Built-in Claude Code tools the agent never gets. It reads untrusted pages and
# bug reports next to the API key, so it has no shell, no way to write files
# and no web access of its own: what it needs comes from its MCP tools.
DISALLOWED_TOOLS = [
    "Bash",
    "BashOutput",
    "KillShell",
    "Write",
    "Edit",
    "NotebookEdit",
    "WebFetch",
    "WebSearch",
    "Task",
    "Agent",
]


PublishFile = Callable[[str, Path, str | None], str]

# How the harness runs the agent: `investigate` a new report, or `retest` an old
# one with Firefox's ETP exception list turned off, to see whether the site
# still breaks without the exceptions it may already have.
Mode = Literal["investigate", "retest"]

RETEST_NOTE = """\
This is a retest of an older report. In this Firefox, the ETP exception list is
disabled in every mode (`set_etp_mode` keeps it off; don't change the
`privacy.trackingprotection.allow_list.*` prefs): neither `baseline` nor
`convenience` entries apply, while the skip-URL prefs still work for testing.
Follow your task procedure as usual. `find_etp_exceptions` still shows the live
list, so in the summary name the existing entries for this bug or site, and say
whether the breakage still happens without them (they are still needed) or not
(the site no longer needs them). In step 3, an existing exception cannot explain
a site that works here."""

# Screenshot stage -> result field holding the path the agent saved it to.
SCREENSHOT_FIELDS = {
    "standard": "screenshot_standard",
    "strict": "screenshot_strict",
    "easyprivacy": "screenshot_easyprivacy",
    "easylist": "screenshot_easylist",
    "fixed": "screenshot_fixed",
}

# Where get_content_blocking_log records every log it returns, by ETP mode.
CONTENT_BLOCKING_LOG_FILE = "content-blocking-log.json"


class AutowebcompatEtpResult(EtpFindings):
    exception_records: list[dict[str, Any]]
    whiteboard_tags: list[str]
    user_story_lines: list[str]
    existing_exceptions: list[dict[str, Any]] | None
    existing_shims: list[dict[str, Any]] | None
    screenshot_urls: dict[str, str]
    content_blocking_log_url: str | None = None
    mode: Mode = "investigate"
    # The Firefox the run drove (None if its application.ini couldn't be read,
    # and in runs from before they were recorded).
    firefox_version: str | None = None
    firefox_build_id: str | None = None


class EtpInvestigation(Task[EtpInvestigationResult]):
    name = "etp_investigation"
    result_cls = EtpInvestigationResult
    result_server_name = RESULT_SERVER_NAME

    def __init__(
        self,
        task_config: TaskConfig,
        run_tracker: RunTracker,
        firefox_path: Path,
        input_data: AutoWebcompatInput,
        bugzilla_mcp_server: McpServerConfig,
        screenshot_dir: Path,
        content_blocking_log_file: Path | None = None,
        mode: Mode = "investigate",
    ):
        super().__init__(task_config, run_tracker)
        self.allowed_tools.remove("Bash")
        self.input_data = input_data
        self.mode = mode
        self.screenshot_paths = {
            stage: screenshot_dir / f"{stage}.png" for stage in SCREENSHOT_FIELDS
        }
        self.add_mcp_server(
            "firefox-devtools",
            build_firefox_devtools_server(
                firefox_path,
                headless=task_config.headless,
                content_blocking_log_file=content_blocking_log_file,
                disable_exceptions=mode == "retest",
            ),
            DEVTOOLS_TOOLS + PRIVILEGED_TOOLS,
        )
        self.add_mcp_server(
            EXCEPTIONS_SERVER_NAME, build_exceptions_server(), [FIND_EXCEPTIONS_TOOL]
        )
        self.add_mcp_server(
            TRACKER_LISTS_SERVER_NAME,
            build_tracker_lists_server(),
            [CHECK_TRACKER_LISTS_TOOL],
        )
        self.add_mcp_server(SHIMS_SERVER_NAME, build_shims_server(), [FIND_SHIMS_TOOL])
        if self.input_data.type == "bug_id":
            self.add_mcp_server("bugzilla", bugzilla_mcp_server, BUGZILLA_READ_TOOLS)

    def agent_options(self) -> ClaudeAgentOptions:
        options = super().agent_options()
        # The run bypasses permission prompts, so leaving a tool out of
        # allowed_tools doesn't keep the agent from calling it; this does.
        options.disallowed_tools = list(DISALLOWED_TOOLS)
        return options

    def subject(self) -> Any:
        return self.input_data.subject()

    def system_prompt(self) -> str:
        return (HERE / "prompts" / "system.md").read_text()

    def user_prompt(self) -> str:
        if isinstance(self.input_data, BugDataInput):
            report = (
                "Here is the web-compatibility report to work on:\n\n"
                f"{self.input_data.bug_data}\n\n"
                "Follow your task procedure."
            )
        else:
            report = (
                f"The web-compatibility report to work on is Bugzilla bug {self.input_data.bug_id}. "
                "Fetch it using the Bugzilla MCP tools, then follow your task procedure."
            )
        if self.mode == "retest":
            report = f"{report}\n\n{RETEST_NOTE}"
        paths = self.screenshot_paths
        return (
            f"{report}\n\n"
            "Screenshot paths (pass these exactly as `saveTo`):\n"
            f"- Standard mode: {paths['standard']}\n"
            f"- Strict mode: {paths['strict']}\n"
            f"- Strict mode with the EasyPrivacy list: {paths['easyprivacy']}\n"
            f"- Strict mode with EasyList and EasyPrivacy: {paths['easylist']}\n"
            f"- Strict mode with the fix: {paths['fixed']}"
        )


def exception_records(
    result: EtpInvestigationResult, input_data: AutoWebcompatInput
) -> list[dict[str, Any]]:
    """Turn the proposals into records shaped like the Remote Settings collection."""
    bug_ids = [str(input_data.bug_id)] if isinstance(input_data, BugIdInput) else []
    records = []
    for proposal in result.proposed_exceptions:
        record: dict[str, Any] = {
            "bugIds": bug_ids,
            "category": proposal.category,
            "urlPattern": proposal.urlPattern,
            "classifierFeatures": proposal.classifierFeatures,
        }
        if proposal.topLevelUrlPattern is not None:
            record["topLevelUrlPattern"] = proposal.topLevelUrlPattern
        records.append(record)
    return records


def trackers_blocked_host(pattern: str) -> str:
    """The host of a match pattern, keeping a leading `*.` wildcard."""
    match = re.match(r"^[^:]+://([^/:]+)", pattern)
    return match.group(1) if match else pattern


def bug_tags(result: EtpInvestigationResult) -> tuple[list[str], list[str]]:
    """The whiteboard tags and user story lines that mark the bug as diagnosed.

    Follows https://wiki.mozilla.org/Privacy/Docs/How_To_Triage_ETP-Strict_Website_Breakage:
    the Remote Settings bot picks up diagnosed bugs with an `[exception-*]`
    category tag and the hosts and classifier features in the user story.
    Returns empty lists unless the agent proposes exceptions.
    """
    proposals = result.proposed_exceptions
    if not proposals:
        return [], []
    categories = [
        category
        for category in ("baseline", "convenience")
        if any(p.category == category for p in proposals)
    ]
    if len(categories) > 1:
        logger.warning(
            "proposed exceptions mix baseline and convenience; the bug's tags "
            "can only carry one category"
        )
    hosts = list(dict.fromkeys(trackers_blocked_host(p.urlPattern) for p in proposals))
    features = [
        feature
        for feature in get_args(ClassifierFeature)
        if any(feature in p.classifierFeatures for p in proposals)
    ]
    whiteboard = ["[privacy-team:diagnosed]"] + [f"[exception-{c}]" for c in categories]
    user_story = [
        "trackers-blocked:" + ",".join(hosts),
        "classifier-features:" + ",".join(features),
    ]
    return whiteboard, user_story


def find_existing_shims(shim: ProposedShim | None) -> list[dict[str, Any]] | None:
    """Existing shims whose URL patterns cover a host the proposal touches.

    Returns None if `shims.js` couldn't be fetched.
    """
    if shim is None:
        return []
    try:
        response = requests.get(SHIMS_URL, timeout=60)
        response.raise_for_status()
        existing = parse_shims(response.text)
    except requests.RequestException, ValueError:
        logger.exception("could not fetch or parse the existing shims")
        return None

    matches = matching_shims(
        existing, {pattern_host(p) for p in shim.matches + shim.unblocksOnOptIn}
    )

    found = [match["id"] for match in matches]
    if found and shim.existing_shim_id not in found:
        logger.warning(
            "agent proposed shim with existing_shim_id=%s, but %s already cover "
            "its hosts",
            shim.existing_shim_id,
            ", ".join(found),
        )
    return matches


def publish_screenshots(
    result: EtpInvestigationResult, publish_file: PublishFile
) -> dict[str, str]:
    """Publish the screenshots the agent saved; return their URLs by stage."""
    urls = {}
    for stage, field in SCREENSHOT_FIELDS.items():
        path = getattr(result, field)
        if path is not None:
            urls[stage] = publish_file(f"screenshot-{stage}.png", path, "image/png")
    return urls


def publish_content_blocking_log(path: Path, publish_file: PublishFile) -> str | None:
    """Publish the content blocking logs the MCP server recorded, if any."""
    if not path.is_file():
        return None
    return publish_file(CONTENT_BLOCKING_LOG_FILE, path, "application/json")


async def run_autowebcompat_etp(
    config: TaskConfig,
    tracker: RunTracker,
    input_data: AutoWebcompatInput,
    bugzilla_mcp_server: McpServerConfig,
    publish_file: PublishFile,
    mode: Mode = "investigate",
    run_preflight: bool = True,
) -> AutowebcompatEtpResult:
    """Investigate an ETP breakage report and return the proposed fix.

    In `retest` mode, Firefox runs with the ETP exception list turned off. With
    `run_preflight`, the URL is loaded first, and a site Firefox shows an error
    page for ends the run without starting the agent.

    Raises :class:`AgentError` if the agent ends in an error.
    """
    # The screenshots and the content blocking log are kept until they are
    # published below, then removed with the directory.
    with tempfile.TemporaryDirectory(
        prefix="autowebcompat-etp-", ignore_cleanup_errors=True
    ) as tmp:
        work = Path(tmp)
        with Environment() as env:
            if not config.headless:
                env.start_xvfb()

            firefox_path = install_firefox("nightly")
            firefox_version, firefox_build_id = firefox_build(firefox_path)
            logger.info("Firefox %s, build %s", firefox_version, firefox_build_id)
            screenshot_dir = work / "screenshots"
            log_dir = work / "logs"
            screenshot_dir.mkdir()
            log_dir.mkdir()
            content_blocking_log_file = log_dir / CONTENT_BLOCKING_LOG_FILE
            investigation = EtpInvestigation(
                config,
                tracker,
                firefox_path,
                input_data,
                bugzilla_mcp_server,
                screenshot_dir,
                content_blocking_log_file,
                mode,
            )
            stopped = (
                await preflight(
                    input_data,
                    investigation.mcp_servers["firefox-devtools"],
                    investigation.screenshot_paths["standard"],
                )
                if run_preflight
                else None
            )
            result = stopped if stopped is not None else await investigation.run()

        records = exception_records(result, input_data)
        whiteboard_tags, user_story_lines = bug_tags(result)
        return AutowebcompatEtpResult(
            # The local paths mean nothing outside the container; the URLs replace them.
            **result.model_dump(exclude=set(SCREENSHOT_FIELDS.values())),
            exception_records=records,
            whiteboard_tags=whiteboard_tags,
            user_story_lines=user_story_lines,
            existing_exceptions=find_existing_exceptions(records),
            existing_shims=find_existing_shims(result.proposed_shim),
            screenshot_urls=publish_screenshots(result, publish_file),
            content_blocking_log_url=publish_content_blocking_log(
                content_blocking_log_file, publish_file
            ),
            mode=mode,
            firefox_version=firefox_version,
            firefox_build_id=firefox_build_id,
        )
