"""Load the reported URL once before the agent starts, at no model cost.

If Firefox shows its own error page for the site (a certificate error, a DNS
failure, a blocked page), or the site answers with an error status that leaves
nothing to look at (a bot-protection or captcha page, a geo-block, a server
error), there is nothing for the agent to test, so the run stops with a result
saying so instead of paying for agent turns.
"""

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import requests
from autowebcompat_tools.inputs import AutoWebcompatInput, BugIdInput
from claude_agent_sdk.types import McpStdioServerConfig
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .result import EtpInvestigationResult

logger = logging.getLogger("autowebcompat-etp")

BUGZILLA_BUG_URL = "https://bugzilla.mozilla.org/rest/bug/{}"
PREFLIGHT_TIMEOUT = 90
# How long a page that answered with a blocking status gets to finish a check
# that passes on its own (a proof-of-work interstitial) and load the site.
CHALLENGE_WAIT_SECONDS = 20

# The pages Firefox shows instead of a site it couldn't or wouldn't load.
ERROR_PAGES = ("about:certerror", "about:neterror", "about:blocked")

# about:neterror codes (the `e=` query) that mean the site can't be reached.
DOWN_CODES = {
    "dnsNotFound",
    "connectionFailure",
    "netTimeout",
    "netOffline",
    "netReset",
    "netInterrupt",
    "proxyConnectFailure",
    "proxyResolveFailure",
}

# What the site's own answer means when it leaves nothing to test. Other
# statuses (404 included: some single-page apps boot from their 404 page) go to
# the agent.
BLOCKED_STATUSES = {403: "blocked", 429: "blocked", 451: "blocked_geo"}

URL_RE = re.compile(r"https?://[^\s<>\"'()\[\]]+")

# Reads what the page ended on. Firefox error pages can read their own error
# details: the NSS code (e.g. SEC_ERROR_UNKNOWN_ISSUER) for certificate errors.
PAGE_STATE_SCRIPT = """() => {
  const state = {
    uri: document.documentURI,
    code: null,
    // The HTTP status of the document the tab is showing.
    status: performance.getEntriesByType("navigation")[0]?.responseStatus || null,
    title: document.title,
    // A captcha or "are you human" page, as opposed to a plain refusal.
    captcha:
      /just a moment|attention required|are you (a )?(human|robot)|verify (that )?you are human|security check|pardon our interruption|captcha/i.test(
        document.title
      ) ||
      !!document.querySelector(
        'iframe[src*="captcha" i], iframe[src*="challenges.cloudflare.com"], ' +
          '[id*="captcha" i], [class*="captcha" i], #challenge-form'
      ),
  };
  for (const read of [
    () => document.getFailedCertSecurityInfo().errorCodeString,
    () => document.getNetErrorInfo().errorCodeString,
    () => document.getElementById("errorCode").textContent.trim(),
  ]) {
    try {
      const code = read();
      if (code) { state.code = code; break; }
    } catch (e) {}
  }
  return state;
}"""


@dataclass
class ErrorPage:
    url: str
    document_uri: str
    # The `e=` code from the error page's URI, e.g. dnsNotFound or nssBadCert.
    error: str | None
    # The detailed code the page reports, e.g. SEC_ERROR_UNKNOWN_ISSUER.
    code: str | None
    screenshot: Path | None

    @property
    def failure_reason(self) -> Literal["down", "other"]:
        return "down" if self.error in DOWN_CODES else "other"

    def describe(self) -> str:
        page = self.document_uri.split("?", 1)[0]
        kind = {
            "about:certerror": "a certificate error",
            "about:blocked": "a blocked-site page",
        }.get(page, "a network error")
        codes = " / ".join(c for c in (self.code, self.error) if c) or page
        return f"Firefox shows {kind} ({codes}) for {self.url}, so ETP was not tested."


@dataclass
class BlockedPage:
    """A page the site answered with a blocking or server-error status."""

    url: str
    status: int
    captcha: bool
    screenshot: Path | None

    @property
    def failure_reason(
        self,
    ) -> Literal["blocked", "blocked_captcha", "blocked_geo", "down"]:
        if self.status >= 500:
            # Bot protection also answers 503 with its challenge page.
            return "blocked_captcha" if self.captcha else "down"
        reason = BLOCKED_STATUSES[self.status]
        return "blocked_captcha" if self.captcha and reason == "blocked" else reason

    def describe(self) -> str:
        kind = {
            "blocked": "the site refuses this browser",
            "blocked_captcha": "a captcha or bot-protection page",
            "blocked_geo": "the site is unavailable for legal reasons here",
            "down": "a server error",
        }[self.failure_reason]
        return (
            f"{self.url} answers with HTTP {self.status} ({kind}), "
            "so ETP was not tested."
        )


def blocking_status(state: dict) -> int | None:
    """The page's HTTP status if it leaves nothing to test, else None."""
    status = state.get("status")
    if isinstance(status, int) and (status in BLOCKED_STATUSES or status >= 500):
        return status
    return None


def first_url(text: str) -> str | None:
    match = URL_RE.search(text)
    return match.group(0).rstrip(".,;:!?") if match else None


def bug_url(bug_id: int) -> str | None:
    """The bug's URL field, or None if it is empty, private or unreadable."""
    try:
        response = requests.get(
            BUGZILLA_BUG_URL.format(bug_id),
            params={"include_fields": "url,summary"},
            timeout=10,
        )
        response.raise_for_status()
        bugs = response.json().get("bugs") or []
    except requests.RequestException, ValueError, AttributeError:
        logger.exception("[preflight] could not read bug %s", bug_id)
        return None
    url = bugs[0].get("url") if bugs else None
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return None
    return url.strip()


def target_url(input_data: AutoWebcompatInput) -> str | None:
    if isinstance(input_data, BugIdInput):
        return bug_url(input_data.bug_id)
    return first_url(input_data.bug_data)


def error_page(document_uri: str) -> str | None:
    """The `e=` code if `document_uri` is a Firefox error page ("" if it has none)."""
    if not document_uri.startswith(ERROR_PAGES):
        return None
    match = re.search(r"[?&]e=([^&#]*)", document_uri)
    return match.group(1) if match else ""


def tool_text(result) -> str:
    return "\n".join(
        getattr(item, "text", "") for item in result.content if hasattr(item, "text")
    )


def script_value(text: str):
    """The JSON value evaluate_script returned, from its response text."""
    match = re.search(r"```json\n(.*)\n```", text, re.DOTALL)
    if not match:
        raise ValueError(f"unexpected evaluate_script response: {text[:200]}")
    return json.loads(match.group(1))


async def page_state(session: ClientSession) -> dict:
    evaluated = await session.call_tool(
        "evaluate_script", {"function": PAGE_STATE_SCRIPT}
    )
    if evaluated.isError:
        raise RuntimeError(f"evaluate_script failed: {tool_text(evaluated)}")
    return script_value(tool_text(evaluated))


async def load_page(
    server: McpStdioServerConfig, url: str, screenshot: Path
) -> ErrorPage | BlockedPage | None:
    """Load `url` with the agent's MCP server.

    Returns the error page Firefox shows or the blocking page the site
    answers with, if any.
    """
    params = StdioServerParameters(
        command=server["command"],
        args=server.get("args", []),
        # Like the agent's server: the run's environment plus the config's.
        env={**os.environ, **server.get("env", {})},
    )
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        # Navigating to a site Firefox can't load may report an error; what
        # the tab ended on is read below either way.
        navigated = await session.call_tool("navigate_page", {"url": url})
        if navigated.isError:
            logger.info("[preflight] navigate_page: %s", tool_text(navigated))
        await session.call_tool("wait_for_page_load", {"timeoutSeconds": 20})
        state = await page_state(session)
        if blocking_status(state) is not None:
            # A check that passes on its own reloads into the site; give it
            # the time the agent would, then look again.
            logger.info(
                "[preflight] %s answered HTTP %s; waiting for it to change",
                url,
                state.get("status"),
            )
            await session.call_tool(
                "wait_for_page_load", {"timeoutSeconds": CHALLENGE_WAIT_SECONDS}
            )
            state = await page_state(session)
        document_uri = state.get("uri") or ""
        error = error_page(document_uri)
        status = blocking_status(state)
        logger.info(
            "[preflight] %s ended on %s (HTTP %s)",
            url,
            document_uri,
            state.get("status"),
        )
        if error is None and status is None:
            return None
        shot = await session.call_tool("screenshot_page", {"saveTo": str(screenshot)})
        saved = screenshot if not shot.isError and screenshot.is_file() else None
        if error is None:
            return BlockedPage(
                url=url,
                status=status,
                captcha=bool(state.get("captcha")),
                screenshot=saved,
            )
        return ErrorPage(
            url=url,
            document_uri=document_uri,
            error=error or None,
            code=state.get("code"),
            screenshot=saved,
        )


async def preflight(
    input_data: AutoWebcompatInput,
    server: McpStdioServerConfig,
    screenshot: Path,
    timeout: float = PREFLIGHT_TIMEOUT,
) -> EtpInvestigationResult | None:
    """A result for a site that shows an error or blocking page, else None.

    None means the agent should run: the site loaded, there was no URL to
    check, or the preflight itself failed.
    """
    url = target_url(input_data)
    if url is None:
        logger.info("[preflight] no URL to check; skipping")
        return None
    try:
        async with asyncio.timeout(timeout):
            page = await load_page(server, url, screenshot)
    except Exception:
        logger.exception("[preflight] failed; running the agent")
        return None
    if page is None:
        logger.info("[preflight] %s loaded; running the agent", url)
        return None
    summary = page.describe()
    logger.info("[preflight] %s Not starting the agent.", summary)
    return EtpInvestigationResult(
        summary=summary,
        failure_reason=page.failure_reason,
        broken_in_standard=None,
        broken_in_strict=None,
        broken_with_easyprivacy=None,
        broken_with_easylist=None,
        culprit_prefs=[],
        blocked_resources=[],
        proposed_exceptions=[],
        exception_verified=False,
        severity=None,
        priority=None,
        steps=f"1. Load {url} in Firefox.",
        screenshot_standard=page.screenshot,
    )
