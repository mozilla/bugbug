"""Compare a site with and without tracker blocking.

Loads the site with no tracker blocking, in Strict ETP, and in Strict ETP with
the EasyPrivacy list and with EasyList and EasyPrivacy instead of Firefox's
lists, and takes a full-page screenshot of each load. If the screenshots are
identical, the site is the same in every configuration and the model is never
called. Otherwise an agent works out whether the differences are variation
between loads or breakage, and in which configurations. Either way the result
has the number of trackers each configuration blocked.
"""

import logging
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, get_args

from autowebcompat_tools.environment import Environment
from autowebcompat_tools.task import RunTracker, Task, TaskConfig
from claude_agent_sdk.types import McpStdioServerConfig
from hackbot_agents.autowebcompat_etp.browser import install_firefox_nightly
from hackbot_agents.autowebcompat_etp.config import DEVTOOLS_TOOLS
from hackbot_agents.autowebcompat_etp.mcp_servers import (
    build_firefox_devtools_server,
)

from .capture import Capture, capture_configurations
from .compare import diff_screenshots
from .result import (
    CONFIGURATION_LABELS,
    RESULT_SERVER_NAME,
    BlockingConfiguration,
    ComparisonVerdict,
    Configuration,
    EtpComparisonResult,
    ScreenshotDiff,
)

HERE = Path(__file__).resolve().parent

logger = logging.getLogger("autowebcompat-etp-compare")

PublishFile = Callable[[str, Path, str | None], str]

# The privileged tools the comparison needs; it changes no prefs itself.
COMPARE_PRIVILEGED_TOOLS = [
    "mcp__firefox-devtools__set_etp_mode",
    "mcp__firefox-devtools__get_content_blocking_log",
    "mcp__firefox-devtools__wait_for_page_load",
    "mcp__firefox-devtools__screenshot_full_page",
]

CONTENT_BLOCKING_LOG_FILE = "content-blocking-log.json"


def describe_diff(diff: ScreenshotDiff) -> str:
    if diff.identical:
        return "identical to `off`"
    if diff.bbox is None:
        return (
            f"different page size: {diff.size[0]}x{diff.size[1]} vs "
            f"{diff.reference_size[0]}x{diff.reference_size[1]} in `off`"
        )
    assert diff.changed_fraction is not None
    return (
        f"{diff.changed_fraction:.1%} of the pixels differ from `off`, within "
        f"the box (left, top, right, bottom) {diff.bbox}"
    )


def blocked_trackers_summary(captures: dict[Configuration, Capture]) -> str:
    return "; ".join(
        f"{CONFIGURATION_LABELS[c]}: {capture.blocked_trackers}"
        for c, capture in captures.items()
    )


def needs_review(
    captures: dict[Configuration, Capture],
    diffs: dict[BlockingConfiguration, ScreenshotDiff],
) -> bool:
    """Whether the model has to look at the loads.

    Identical screenshots only show the site is the same if every load
    reached the page: a site that is down shows the same error everywhere.
    """
    return any(capture.navigation_failed for capture in captures.values()) or not all(
        diff.identical for diff in diffs.values()
    )


class EtpComparison(Task[ComparisonVerdict]):
    name = "etp_comparison"
    result_cls = ComparisonVerdict
    result_server_name = RESULT_SERVER_NAME

    def __init__(
        self,
        task_config: TaskConfig,
        run_tracker: RunTracker,
        firefox_server: McpStdioServerConfig,
        url: str,
        captures: dict[Configuration, Capture],
        diffs: dict[BlockingConfiguration, ScreenshotDiff],
        diff_paths: dict[BlockingConfiguration, Path],
        recheck_dir: Path,
    ):
        super().__init__(task_config, run_tracker)
        self.url = url
        self.captures = captures
        self.diffs = diffs
        self.diff_paths = diff_paths
        self.recheck_dir = recheck_dir
        self.add_mcp_server(
            "firefox-devtools",
            firefox_server,
            DEVTOOLS_TOOLS + COMPARE_PRIVILEGED_TOOLS,
        )

    def subject(self) -> Any:
        return self.url

    def system_prompt(self) -> str:
        return (HERE / "prompts" / "system.md").read_text()

    def user_prompt(self) -> str:
        lines = [f"The site is {self.url}.", ""]
        for configuration, capture in self.captures.items():
            lines.append(f"## `{configuration}`: {CONFIGURATION_LABELS[configuration]}")
            lines.append(f"- Screenshot: {capture.screenshot}")
            if not capture.full_page:
                lines.append(
                    "  (viewport only: the full-page capture failed for this load)"
                )
            lines.append(f"- Capture: {capture.screenshot_note}")
            lines.append(f"- Blocked trackers: {capture.blocked_trackers}")
            lines.append(f"- Load: {capture.load_status}")
            if configuration in self.diffs:
                lines.append(f"- Compared: {describe_diff(self.diffs[configuration])}")
            if configuration in self.diff_paths:
                lines.append(f"- Diff image: {self.diff_paths[configuration]}")
            lines.append("")
        lines.append(
            f"Save the screenshots of your own loads in {self.recheck_dir}, named "
            "after the configuration and a counter (e.g. `off-1.png`)."
        )
        lines.append("")
        lines.append("Follow your task procedure.")
        return "\n".join(lines)


def publish_images(
    captures: dict[Configuration, Capture],
    diff_paths: dict[BlockingConfiguration, Path],
    recheck_dir: Path,
    publish_file: PublishFile,
) -> dict[str, str]:
    """Publish the screenshots and diff images; return their URLs by name."""
    images = {c: capture.screenshot for c, capture in captures.items()}
    images |= {f"diff-{c}": path for c, path in diff_paths.items()}
    images |= {f"recheck-{p.stem}": p for p in sorted(recheck_dir.glob("*.png"))}
    return {
        name: publish_file(f"screenshot-{name}.png", path, "image/png")
        for name, path in images.items()
        if path.is_file()
    }


async def run_autowebcompat_etp_compare(
    config: TaskConfig,
    tracker: RunTracker,
    url: str,
    publish_file: PublishFile,
) -> EtpComparisonResult:
    """Compare `url` across the configurations and report what differs."""
    with Environment() as env:
        if not config.headless:
            env.start_xvfb()

        firefox_path = install_firefox_nightly()
        work_dir = Path(tempfile.mkdtemp(prefix="autowebcompat-etp-compare-"))
        screenshot_dir = work_dir / "screenshots"
        recheck_dir = work_dir / "recheck"
        screenshot_dir.mkdir()
        recheck_dir.mkdir()
        content_blocking_log_file = work_dir / CONTENT_BLOCKING_LOG_FILE
        firefox_server = build_firefox_devtools_server(
            firefox_path,
            headless=config.headless,
            content_blocking_log_file=content_blocking_log_file,
        )

        captures = await capture_configurations(firefox_server, url, screenshot_dir)
        reference = captures["off"].screenshot
        diffs: dict[BlockingConfiguration, ScreenshotDiff] = {}
        diff_paths: dict[BlockingConfiguration, Path] = {}
        for configuration in get_args(BlockingConfiguration):
            diff_path = screenshot_dir / f"diff-{configuration}.png"
            diffs[configuration] = diff_screenshots(
                reference, captures[configuration].screenshot, diff_path
            )
            if diff_path.is_file():
                diff_paths[configuration] = diff_path

        llm_used = needs_review(captures, diffs)
        if llm_used:
            # The capture's Firefox is gone; the agent gets a fresh one.
            verdict = await EtpComparison(
                config,
                tracker,
                firefox_server,
                url,
                captures,
                diffs,
                diff_paths,
                recheck_dir,
            ).run()
        else:
            logger.info("the screenshots are identical; skipping the agent")
            verdict = ComparisonVerdict(
                outcome="sites_same",
                broken_configurations=[],
                summary=(
                    "The site renders identically in every configuration. "
                    f"Blocked trackers: {blocked_trackers_summary(captures)}."
                ),
            )

    return EtpComparisonResult(
        url=url,
        outcome=verdict.outcome,
        broken_configurations=verdict.broken_configurations,
        breakage_tags=verdict.breakage_tags,
        summary=verdict.summary,
        blocked_trackers={
            c: capture.blocked_trackers for c, capture in captures.items()
        },
        blocked_tracker_origins={
            c: capture.blocked_tracker_origins for c, capture in captures.items()
        },
        screenshot_diffs=diffs,
        llm_used=llm_used,
        screenshot_urls=publish_images(captures, diff_paths, recheck_dir, publish_file),
        content_blocking_log_url=(
            publish_file(
                CONTENT_BLOCKING_LOG_FILE, content_blocking_log_file, "application/json"
            )
            if content_blocking_log_file.is_file()
            else None
        ),
    )
