from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel

from app.schemas import BugFixInputs


@dataclass(frozen=True)
class AgentSpec:
    name: str
    description: str
    job_name: str
    input_schema: type[BaseModel]
    build_env: Callable[[BaseModel], dict[str, str]]


def _bug_fix_env(inputs: BaseModel) -> dict[str, str]:
    assert isinstance(inputs, BugFixInputs)
    # The bug-fix agent's Job is multi-container: an `agent` container
    # (no tokens) and a `broker` sidecar (holds BZ_API_KEY at deploy time
    # via Secret Manager). The orchestrator only overrides the `agent`
    # container's env per execution — the broker is fully configured at
    # deploy time. The agent reaches the broker on the task's loopback.
    env: dict[str, str] = {
        "BUG_ID": str(inputs.bug_id),
        "BUGZILLA_MCP_URL": "http://127.0.0.1:8765/mcp",
    }
    if inputs.model is not None:
        env["MODEL"] = inputs.model
    if inputs.max_turns is not None:
        env["MAX_TURNS"] = str(inputs.max_turns)
    if inputs.effort is not None:
        env["EFFORT"] = inputs.effort
    return env


AGENT_REGISTRY: dict[str, AgentSpec] = {
    "bug-fix": AgentSpec(
        name="bug-fix",
        description="Investigate a Bugzilla bug and produce a candidate fix patch against the Firefox source tree.",
        job_name="hackbot-agent-bug-fix",
        input_schema=BugFixInputs,
        build_env=_bug_fix_env,
    ),
}
