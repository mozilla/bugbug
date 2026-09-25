import httpx

from agent_tools.registry import ToolError, tool, tools_in
from dataclasses import dataclass
from pydantic import Field
from typing import Annotated, Any


@dataclass
class SentryContext:
    api_token: str
    api_url_base: str
    client: httpx.AsyncClient
    org_name: str


def _sentry_error(e: Exception, what: str, http_status_error: str) -> ToolError:
    """Render a sentry failure as a structured, machine-parseable error."""
    return ToolError(
        f"{what}: {e}",
        payload={"error": http_status_error, "what": what, "message": str(e)},
    )


@tool
async def get_issue_event(
    ctx: SentryContext, 
    issue_id: Annotated[
        str, Field(description="The Sentry issue ID.")
    ],event_id: str):
    """Retrieve an issue event from Sentry."""
    try:
        headers = { "Authorization": f"Bearer {ctx.api_token}" }
        resp = await ctx.client.get(f"{ctx.api_url_base}/organizations/{ctx.org_name}/issues/{issue_id}/events/{event_id}/", headers=headers)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as e:
        # get the http error, falling back to a generic "sentry_error"
        http_status_error = {401: "auth_failed", 403: "access_denied", 404: "not_found"}.get(e.response.status_code, "sentry_error")

        raise _sentry_error(e, "get_issue_event error", http_status_error) from e


TOOLS = tools_in(__name__)