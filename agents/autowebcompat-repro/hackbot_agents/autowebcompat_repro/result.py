"""Structured result reporting for the autowebcompat-repro agent."""

from __future__ import annotations

from claude_agent_sdk import McpServerConfig, create_sdk_mcp_server, tool
from pydantic import BaseModel, Field, ValidationError

RESULT_SERVER_NAME = "autowebcompat-repro"
SUBMIT_RESULT_TOOL = f"mcp__{RESULT_SERVER_NAME}__submit_result"


class ReproductionResult(BaseModel):
    """Canonical result the agent produces for a web-compat investigation."""

    reproduced: bool = Field(
        description=(
            "true if the reported issue reproduced in Firefox, otherwise false."
        ),
    )
    summary: str = Field(
        description="A concise account of what you observed.",
    )
    steps: str = Field(
        description=(
            "The ordered steps you took, as a single numbered list (1., 2., 3., "
            "... one step per line), written so another agent could reproduce "
            "them with no extra context. Each step must be self-contained: "
            "whenever you introduce an input or artifact the report did not "
            "provide (a file, image, account, or any other test data), state its "
            "exact origin — the URL you fetched it from, the command you ran, or "
            'how you generated it — not just that you "used" or "saved" it. A '
            "reader must be able to obtain the same inputs."
        ),
    )
    chrome_mask_fixed: bool | None = Field(
        description=(
            "Whether enabling the Chrome Mask extension (spoofing a Chrome "
            "User-Agent) fixed the reported behavior: true if it fixed it, "
            "false if it did not, null if the Chrome Mask test was not run "
            "(e.g. the issue did not reproduce at baseline)."
        ),
    )


SUBMIT_RESULT_SCHEMA = {
    **ReproductionResult.model_json_schema(),
    "additionalProperties": False,
}


class ResultCollector:
    """Holds the result submitted by the agent, if any."""

    def __init__(self) -> None:
        self.result: ReproductionResult | None = None


def build_result_server(collector: ResultCollector) -> McpServerConfig:
    """Build an in-process MCP server exposing the ``submit_result`` tool.

    The handler validates the payload against :class:`ReproductionResult` and stores
    it on ``collector``. A validation error is returned to the model (as tool
    output) so it can correct and resubmit rather than failing the run.
    """

    @tool(
        "submit_result",
        "Submit the final web-compatibility investigation result. Call exactly "
        "once, at the end, after completing the investigation.",
        SUBMIT_RESULT_SCHEMA,
    )
    async def submit_result(args: dict) -> dict:
        try:
            collector.result = ReproductionResult.model_validate(args)
        except ValidationError as exc:
            return {
                "content": [{"type": "text", "text": f"Invalid result: {exc}"}],
                "is_error": True,
            }
        return {"content": [{"type": "text", "text": "Result recorded."}]}

    return create_sdk_mcp_server(name=RESULT_SERVER_NAME, tools=[submit_result])
