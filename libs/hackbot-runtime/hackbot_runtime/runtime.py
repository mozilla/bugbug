import asyncio
import inspect
import logging
import sys
import traceback
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import NoReturn

from pydantic import ValidationError

from hackbot_runtime import anthropic_wif, wandb_wif
from hackbot_runtime.config import HackbotConfig, load_config
from hackbot_runtime.context import HackbotContext
from hackbot_runtime.results import HackbotAgentResult
from hackbot_runtime.tracing import trace_agent

log = logging.getLogger("hackbot_runtime")

# An agent's main() returns a HackbotAgentResult on success; to fail the run it
# raises (AgentError, or any exception). The runtime turns that outcome into
# summary.json + an exit code.
Findings = HackbotAgentResult
AgentMain = Callable[[HackbotContext], Findings]
AsyncAgentMain = Callable[[HackbotContext], Awaitable[Findings]]

# What run()/run_async() accept to locate an agent's hackbot.toml: a path to it,
# an already-parsed config, or None to auto-discover ``hackbot.toml`` (in the
# working directory or above the entry point's module).
ConfigArg = Path | HackbotConfig | None

_CONFIG_NAME = "hackbot.toml"
_SUMMARY_NAME = "summary.json"
_AGENT_LOG_KEY = "logs/agent.log"


def _configure_auth() -> None:
    """Set up the model-provider and observability credentials before the run.

    Both Anthropic (model access) and W&B (Weave tracing) support GCP Workload
    Identity Federation, so in deployment neither needs a long-lived key in the
    agent container; both fall back to their API-key env var locally.
    """
    if anthropic_wif.configure():
        log.info("Configured Anthropic WIF authentication")
    if wandb_wif.configure():
        log.info("Configured W&B WIF authentication")


def _configure_logging() -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            stream=sys.stderr,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )


def _ok_payload(ctx: HackbotContext, findings: dict) -> dict:
    # Actions are recorded via ctx.actions; the agent never carries them.
    return {
        "status": "ok",
        "error": None,
        "findings": findings,
        "actions": ctx.actions.actions,
    }


def _error_payload(
    ctx: HackbotContext, error: str, *, traceback_str: str | None = None
) -> dict:
    return {
        "status": "error",
        "error": error,
        "findings": {"traceback": traceback_str} if traceback_str else {},
        "actions": ctx.actions.actions,
    }


def _discover_config_path(entrypoint: Callable) -> Path | None:
    """Locate ``hackbot.toml`` for an agent that didn't pass one explicitly.

    Agents keep ``hackbot.toml`` at their agent root (alongside ``pyproject.toml``
    / ``Dockerfile``), above the ``hackbot_agents`` package. Two layouts to cover:

    - **Deployed image**: the package is installed into site-packages, but the
      Dockerfile copies ``hackbot.toml`` into the working directory — so check
      the cwd first.
    - **Editable checkout / tests**: the entry point's module lives under the
      agent root, so walk up from it until the toml turns up.
    """
    cwd_candidate = Path.cwd() / _CONFIG_NAME
    if cwd_candidate.exists():
        return cwd_candidate
    try:
        module_file = inspect.getsourcefile(entrypoint)
    except TypeError:
        module_file = None
    if module_file:
        for parent in Path(module_file).resolve().parents:
            candidate = parent / _CONFIG_NAME
            if candidate.exists():
                return candidate
    return None


def _resolve_config(entrypoint: Callable, config: ConfigArg) -> HackbotConfig:
    if isinstance(config, HackbotConfig):
        return config
    path = config if isinstance(config, Path) else _discover_config_path(entrypoint)
    return load_config(path) if path else HackbotConfig()


def _load_hackbot(entrypoint: Callable, config: ConfigArg) -> HackbotContext | None:
    """Build the HackbotContext (and its inner env-derived Context).

    ``config`` may be a path to a ``hackbot.toml``, an already-parsed
    :class:`HackbotConfig`, or ``None`` to auto-discover the toml (cwd or above
    the entry point's module), falling back to an empty config when there's none.
    """
    parsed = _resolve_config(entrypoint, config)
    try:
        return HackbotContext.from_config_obj(parsed)
    except ValidationError as exc:
        log.error(
            "Failed to load HackbotContext from env; no summary can be written.\n%s",
            exc,
        )
        return None


def _publish_log(ctx: HackbotContext) -> None:
    """Publish the run log under the canonical key, if the agent wrote one."""
    if ctx.log_path.exists():
        ctx.publish_file(_AGENT_LOG_KEY, ctx.log_path, "text/plain")


def _finish(ctx: HackbotContext, outcome: object) -> int:
    """Write summary.json from the agent's outcome and return the exit code.

    ``outcome`` is the agent's :class:`HackbotAgentResult` on success, or the
    exception it raised on failure.
    """
    if isinstance(outcome, BaseException):
        payload = _error_payload(
            ctx,
            f"{type(outcome).__name__}: {outcome}",
            traceback_str=traceback.format_exc(),
        )
        exit_code = 1
    elif isinstance(outcome, HackbotAgentResult):
        payload = _ok_payload(ctx, outcome.model_dump())
        exit_code = 0
    else:
        # Contract violation: not a HackbotAgentResult or an exception.
        msg = f"Agent returned {type(outcome).__name__}; expected a HackbotAgentResult"
        log.error(msg)
        payload = _error_payload(ctx, msg)
        exit_code = 1

    try:
        _publish_log(ctx)
    except Exception:
        log.exception("Failed to publish agent log")

    try:
        ctx.publish_changes()
    except Exception:
        log.exception("Failed to publish source changes")

    # Upload when a signed policy is configured, else write into the local
    # artifacts dir (so local/compose/direct runs leave it on the host).
    try:
        ctx.publish_json(_SUMMARY_NAME, payload)
    except Exception:
        log.exception("Failed to publish summary.json")
        return 1 if exit_code == 0 else exit_code

    if ctx.uploader is None:
        log.info(
            "RESULTS_POLICY_URL not configured; wrote summary to %s.",
            ctx.run_artifacts_dir / _SUMMARY_NAME,
        )

    return exit_code


def run(entrypoint: AgentMain, config: ConfigArg = None) -> NoReturn:
    _configure_logging()
    ctx = _load_hackbot(entrypoint, config)
    if ctx is None:
        raise SystemExit(2)

    try:
        _configure_auth()
        with trace_agent(entrypoint):
            outcome: object = entrypoint(ctx)
    except Exception as exc:
        log.exception("Agent raised an exception")
        outcome = exc

    raise SystemExit(_finish(ctx, outcome))


def run_async(entrypoint: AsyncAgentMain, config: ConfigArg = None) -> NoReturn:
    _configure_logging()
    ctx = _load_hackbot(entrypoint, config)
    if ctx is None:
        raise SystemExit(2)

    try:
        _configure_auth()
        with trace_agent(entrypoint):
            outcome: object = asyncio.run(entrypoint(ctx))
    except Exception as exc:
        log.exception("Agent raised an exception")
        outcome = exc

    raise SystemExit(_finish(ctx, outcome))
