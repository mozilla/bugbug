import asyncio
import logging
import sys
import traceback
from collections.abc import Awaitable, Callable

from pydantic import ValidationError

from hackbot_runtime.context import Context
from hackbot_runtime.result import AgentResult

log = logging.getLogger("hackbot_runtime")

AgentMain = Callable[[Context], AgentResult]
AsyncAgentMain = Callable[[Context], Awaitable[AgentResult]]

_SUMMARY_NAME = "summary.json"


def _configure_logging() -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            stream=sys.stderr,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )


def _summary_payload_from_result(result: AgentResult, ctx: Context) -> dict:
    # Actions are recorded via Context.actions; the result never carries them.
    return {
        "status": result.status,
        "error": result.error,
        "findings": result.findings,
        "actions": ctx.actions.actions,
    }


def _summary_payload_from_exception(exc: BaseException, ctx: Context) -> dict:
    return {
        "status": "error",
        "error": f"{type(exc).__name__}: {exc}",
        "findings": {"traceback": traceback.format_exc()},
        "actions": ctx.actions.actions,
    }


def _load_context() -> Context | None:
    try:
        return Context()
    except ValidationError as exc:
        log.error(
            "Failed to load Context from env; no summary can be written.\n%s",
            exc,
        )
        return None


def _finish(ctx: Context, result_or_exc: AgentResult | BaseException) -> int:
    if isinstance(result_or_exc, AgentResult):
        payload = _summary_payload_from_result(result_or_exc, ctx)
        exit_code = result_or_exc.exit_code
    else:
        payload = _summary_payload_from_exception(result_or_exc, ctx)
        exit_code = 1

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


def _validate_result(result: object) -> AgentResult:
    """Coerce arbitrary agent return values into an AgentResult.

    Returning a synthetic AgentResult (rather than letting an exception
    object flow into `_finish`) keeps the summary deterministic: the
    exception path calls `traceback.format_exc()`, which evaluates to
    "NoneType: None" when no exception is active.
    """
    if isinstance(result, AgentResult):
        return result
    msg = f"Agent returned {type(result).__name__}; expected AgentResult"
    log.error(msg)
    return AgentResult(status="error", error=msg, exit_code=1)


def run(entrypoint: AgentMain) -> int:
    _configure_logging()
    ctx = _load_context()
    if ctx is None:
        return 2

    try:
        result = entrypoint(ctx)
    except Exception as exc:
        log.exception("Agent raised an exception")
        return _finish(ctx, exc)

    return _finish(ctx, _validate_result(result))


def run_async(entrypoint: AsyncAgentMain) -> int:
    _configure_logging()
    ctx = _load_context()
    if ctx is None:
        return 2

    try:
        result = asyncio.run(entrypoint(ctx))
    except Exception as exc:
        log.exception("Agent raised an exception")
        return _finish(ctx, exc)

    return _finish(ctx, _validate_result(result))
