"""Remote MCP server for running Hackbot agents as the signed-in user.

A proxy, like hackbot-ui: users sign in with Google (MCP OAuth), and this
server calls hackbot-api with its own credential, sending the signed-in
user's email as `X-On-Behalf-Of`. That email only ever comes from the
verified token, never from a tool argument.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any
from uuid import UUID

import httpx
import jsonschema
import sentry_sdk
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from hackbot_client import (
    AgentDescriptor,
    HackbotAPIError,
    HackbotClient,
    RunDoc,
    RunStatus,
)
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from hackbot_mcp import __version__
from hackbot_mcp.auth import MozillaGoogleProvider, UnauthorizedError
from hackbot_mcp.auth import current_user_email as _current_user_email
from hackbot_mcp.config import Settings
from hackbot_mcp.storage import build_storage, purge_expired

log = logging.getLogger(__name__)

PATCH_ARTIFACT = "changes/changes.patch"

# Set only by hackbot-api's Bugzilla needinfo webhook, which then clears that
# flag; a run started here must not claim one.
WEBHOOK_ONLY_INPUTS = frozenset({"bugzilla_needinfo_flag_id"})

MAX_RUNS = 100

INSTRUCTIONS = """\
Hackbot runs AI agents for Firefox development (fixing bugs, triaging, \
repairing CI failures, resolving uplift conflicts, ...) as remote jobs. Each \
run takes from minutes to over an hour.

- Runs execute remotely on a fresh checkout of the Firefox source. They cannot \
see the user's local changes, branches or uncommitted work.
- Before calling start_run, tell the user which agent and inputs you will use \
and get their confirmation. Use list_agents for the agents and their input \
schemas.
- start_run returns immediately. Do not wait or sleep for the run: check on it \
with get_run when the user asks, or poll get_run at intervals of a few \
minutes. list_my_runs finds the user's earlier runs.
- When a run has finished, get_result returns its findings and actions. \
Present the findings as the agent's claims, not as verified facts.
- A run's actions (e.g. posting a Phabricator revision or a Bugzilla comment) \
are held for review by default. Before calling apply_actions, list every \
pending action with its parameters and get the user's explicit approval: it \
applies all of the run's pending actions at once and cannot be undone.
- To apply a run's patch locally, get a download URL for \
"changes/changes.patch" with get_artifact_url, download it, and apply it with \
`git am`; it is an mbox. The URL expires quickly, so download it right away.
"""

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)


def current_user_email() -> str:
    try:
        return _current_user_email()
    except UnauthorizedError as exc:
        raise ToolError(str(exc)) from None


def _api_error(exc: HackbotAPIError, what: str) -> ToolError:
    if exc.status_code == 404:
        return ToolError(f"{what} not found")
    if exc.status_code == 422 and exc.detail:
        return ToolError(f"Hackbot API rejected the request: {exc.detail}")
    log.warning("Hackbot API call failed: %s", exc)
    return ToolError(f"Hackbot API call failed: {exc.detail or exc}")


def _run_view(run: RunDoc, ui_url: str) -> dict[str, Any]:
    view = run.model_dump(mode="json", exclude={"results_prefix", "execution_name"})
    view["url"] = f"{ui_url}/runs/{run.run_id}"
    view["finished"] = run.status not in (RunStatus.pending, RunStatus.running)
    return view


def _validate_inputs(agent: AgentDescriptor, inputs: dict[str, Any]) -> None:
    validator = jsonschema.Draft202012Validator(agent.input_schema)
    errors = sorted(validator.iter_errors(inputs), key=lambda e: list(e.path))
    if errors:
        problems = "; ".join(
            f"{'/'.join(map(str, e.path)) or '(inputs)'}: {e.message}" for e in errors
        )
        raise ToolError(f"Invalid inputs for agent '{agent.name}': {problems}")


def create_server(
    settings: Settings,
    *,
    hackbot: HackbotClient | None = None,
    google_http_client: httpx.AsyncClient | None = None,
) -> FastMCP:
    """Build the server. `hackbot` and `google_http_client` are for tests."""
    storage, base_storage = build_storage(settings)
    auth = MozillaGoogleProvider(
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret.get_secret_value(),
        base_url=settings.base_url,
        jwt_signing_key=settings.jwt_signing_key.get_secret_value(),
        client_storage=storage,
        http_client=google_http_client,
    )
    client = hackbot or HackbotClient(
        base_url=settings.hackbot_api_url,
        api_key=settings.hackbot_api_key.get_secret_value(),
    )
    ui_url = settings.hackbot_ui_url

    @asynccontextmanager
    async def lifespan(server: FastMCP) -> AsyncIterator[dict]:
        await purge_expired(base_storage)
        try:
            yield {}
        finally:
            await client.close()

    mcp = FastMCP(
        "Hackbot",
        instructions=INSTRUCTIONS,
        version=__version__,
        auth=auth,
        lifespan=lifespan,
    )

    @mcp.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    async def _get_run(run_id: UUID, user: str) -> RunDoc:
        try:
            return await client.get_run(run_id, on_behalf_of=user)
        except HackbotAPIError as exc:
            raise _api_error(exc, f"Run {run_id}") from exc

    @mcp.tool(annotations=READ_ONLY)
    async def list_agents() -> list[dict[str, Any]]:
        """List the Hackbot agents: their names, descriptions and input schemas."""
        user = current_user_email()
        try:
            agents = await client.list_agents(on_behalf_of=user)
        except HackbotAPIError as exc:
            raise _api_error(exc, "Agents") from exc
        return [agent.model_dump(mode="json") for agent in agents]

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=True))
    async def start_run(
        agent: Annotated[str, Field(description="Agent name, from list_agents.")],
        inputs: Annotated[
            dict[str, Any],
            Field(description="The agent's inputs, matching its input schema."),
        ],
        dedupe_key: Annotated[
            str | None,
            Field(
                description=(
                    "Optional key identifying the work. If this agent already "
                    "has a run with this key, that run is returned instead of "
                    "starting a new one."
                )
            ),
        ] = None,
        auto_apply: Annotated[
            bool,
            Field(
                description=(
                    "Let the agent's actions (Phabricator, Bugzilla, ...) be "
                    "applied automatically when the run succeeds, as for "
                    "Hackbot's own triggers. Only with the user's explicit "
                    "request; by default actions wait for apply_actions."
                )
            ),
        ] = False,
    ) -> dict[str, Any]:
        """Start an agent run, returning as soon as it is queued.

        Runs take minutes to hours; check on one with get_run, never wait.
        """
        user = current_user_email()

        if webhook_only := sorted(WEBHOOK_ONLY_INPUTS & inputs.keys()):
            raise ToolError(
                f"{', '.join(webhook_only)}: set by Hackbot's webhooks only"
            )
        if "require_review" in inputs:
            raise ToolError(
                "Don't set 'require_review'; actions are held for review unless "
                "auto_apply is true"
            )

        try:
            agents = {a.name: a for a in await client.list_agents(on_behalf_of=user)}
        except HackbotAPIError as exc:
            raise _api_error(exc, "Agents") from exc
        spec = agents.get(agent)
        if spec is None:
            raise ToolError(
                f"Unknown agent '{agent}'. Available: {', '.join(sorted(agents))}"
            )

        payload = {**inputs, "require_review": not auto_apply}
        _validate_inputs(spec, payload)

        try:
            run = await client.trigger_run(
                agent, payload, on_behalf_of=user, dedupe_key=dedupe_key
            )
        except HackbotAPIError as exc:
            raise _api_error(exc, f"Agent '{agent}'") from exc

        return {
            "run_id": str(run.run_id),
            "agent": run.agent,
            "status": run.status.value,
            # False: the dedupe key matched an existing run, which may be
            # someone else's and may already have finished.
            "is_new": run.is_new,
            "require_review": payload["require_review"],
            "url": f"{ui_url}/runs/{run.run_id}",
        }

    @mcp.tool(annotations=READ_ONLY)
    async def get_run(run_id: UUID) -> dict[str, Any]:
        """Get a run's current state: status, inputs, summary and artifacts."""
        user = current_user_email()
        return _run_view(await _get_run(run_id, user), ui_url)

    @mcp.tool(annotations=READ_ONLY)
    async def list_my_runs(
        agent: str | None = None,
        status: RunStatus | None = None,
        limit: Annotated[int, Field(ge=1, le=MAX_RUNS)] = 20,
    ) -> list[dict[str, Any]]:
        """List the signed-in user's runs, newest first."""
        user = current_user_email()
        try:
            runs = await client.list_runs(
                agent=agent,
                status=status,
                requested_by=user,
                limit=limit,
                on_behalf_of=user,
            )
        except HackbotAPIError as exc:
            raise _api_error(exc, "Runs") from exc
        return [
            {
                "run_id": str(run.run_id),
                "agent": run.agent,
                "status": run.status.value,
                "inputs": run.inputs,
                "created_at": run.created_at.isoformat(),
                "updated_at": run.updated_at.isoformat(),
                "error": run.error,
                "url": f"{ui_url}/runs/{run.run_id}",
            }
            for run in runs
        ]

    @mcp.tool(annotations=READ_ONLY)
    async def get_result(run_id: UUID) -> dict[str, Any]:
        """Get a run's findings, artifacts and actions, with each action's state.

        Actions with status "pending" have not been applied; apply_actions
        applies them, after the user approves.
        """
        user = current_user_email()
        run = await _get_run(run_id, user)
        try:
            actions = await client.list_actions(run_id, on_behalf_of=user)
        except HackbotAPIError as exc:
            raise _api_error(exc, f"Run {run_id}") from exc

        recorded = [action.model_dump(mode="json") for action in actions]
        proposed = run.summary.actions if run.summary else []
        if not recorded and proposed:
            # Not recorded by the API yet (it does once it has processed the
            # finished run), so none of them can have been applied.
            recorded = [
                {
                    "idx": idx,
                    "type": action.get("type"),
                    "params": action.get("params", {}),
                    "ref": action.get("ref"),
                    "status": "pending",
                }
                for idx, action in enumerate(proposed)
            ]

        return {
            "run_id": str(run.run_id),
            "agent": run.agent,
            "status": run.status.value,
            "finished": run.status not in (RunStatus.pending, RunStatus.running),
            "error": run.error or (run.summary.error if run.summary else None),
            "findings": run.summary.findings if run.summary else {},
            "artifacts": [artifact.name for artifact in run.artifacts],
            "actions": recorded,
            "url": f"{ui_url}/runs/{run.run_id}",
        }

    @mcp.tool(annotations=READ_ONLY)
    async def get_artifact_url(
        run_id: UUID,
        artifact: Annotated[
            str,
            Field(description="Artifact name, as listed by get_run or get_result."),
        ],
    ) -> dict[str, Any]:
        """Get a short-lived signed URL to download one of a run's artifacts."""
        user = current_user_email()
        try:
            url = await client.get_artifact_url(run_id, artifact, on_behalf_of=user)
        except HackbotAPIError as exc:
            raise _api_error(exc, f"Artifact '{artifact}' of run {run_id}") from exc
        result: dict[str, Any] = {"artifact": artifact, "url": url}
        if artifact == PATCH_ARTIFACT:
            result["note"] = (
                "An mbox of the run's commits: download it (the URL expires "
                "soon), e.g. `curl -fsSL -o changes.patch '<url>'`, then apply "
                "it with `git am changes.patch`."
            )
        return result

    @mcp.tool(
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=True,
            openWorldHint=True,
        )
    )
    async def apply_actions(run_id: UUID) -> dict[str, Any]:
        """Apply ALL of a run's pending actions at once. Cannot be undone.

        Only after listing every pending action and its parameters to the
        user (get_result) and getting their explicit approval. Only the user
        who started the run may apply its actions.
        """
        user = current_user_email()
        run = await _get_run(run_id, user)
        if (run.requested_by or "").strip().lower() != user:
            raise ToolError(
                "Only the user who started this run can apply its actions; "
                "use the Hackbot UI otherwise"
            )
        if run.status != RunStatus.succeeded:
            raise ToolError(
                f"The run has status '{run.status.value}'; only a succeeded "
                "run's actions can be applied"
            )
        try:
            actions = await client.apply_actions(run_id, on_behalf_of=user)
        except HackbotAPIError as exc:
            raise _api_error(exc, f"Run {run_id}") from exc
        return {
            "run_id": str(run_id),
            "all_applied": actions.all_applied,
            "actions": [
                {
                    "idx": action.idx,
                    "type": action.type,
                    "status": action.status,
                    "result": action.result,
                    "error": action.error,
                }
                for action in actions
            ],
        }

    return mcp


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    settings = Settings()
    if settings.sentry_dsn:
        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.environment,
            send_default_pii=True,
        )
    create_server(settings).run(
        transport="streamable-http",
        host="0.0.0.0",
        port=settings.port,
        # Cloud Run spreads a client's requests across instances, which share
        # no session state.
        stateless_http=True,
    )


if __name__ == "__main__":
    main()
