import json
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel

from app.schemas import (
    AutowebcompatReproInputs,
    BugFixInputs,
    TestPlanGeneratorInputs,
)


@dataclass(frozen=True)
class AgentSpec:
    name: str
    description: str
    job_name: str
    input_schema: type[BaseModel]
    # Optional override for the rare agent whose env vars don't map 1:1 from
    # its input schema. Defaults to ``model_to_env`` (field -> UPPER_SNAKE env).
    build_env: Callable[[BaseModel], dict[str, str]] | None = None


def model_to_env(inputs: BaseModel) -> dict[str, str]:
    """Serialise validated inputs into Cloud Run Job env overrides.

    Each schema field maps to an upper-cased env var (``bug_id`` -> ``BUG_ID``);
    ``None`` fields are skipped, and the agent reads them back via
    ``pydantic_settings.BaseSettings`` (which upper-cases field names by
    default). Lists/dicts are JSON-encoded. Deploy-time constants (e.g. the
    broker loopback URL) are NOT inputs — they belong in the Job's static env
    config, not here.
    """
    env: dict[str, str] = {}
    for name, value in inputs.model_dump(mode="json").items():
        if value is None:
            continue
        if isinstance(value, str):
            env[name.upper()] = value
        elif isinstance(value, (list, dict)):
            env[name.upper()] = json.dumps(value)
        else:
            env[name.upper()] = str(value)
    return env


AGENT_REGISTRY: dict[str, AgentSpec] = {
    "bug-fix": AgentSpec(
        name="bug-fix",
        description="Investigate a Bugzilla bug and produce a candidate fix patch against the Firefox source tree.",
        job_name="hackbot-agent-bug-fix",
        input_schema=BugFixInputs,
    ),
    "autowebcompat-repro": AgentSpec(
        name="autowebcompat-repro",
        description=(
            "Reproduce a Firefox web-compatibility issue in headless Firefox "
            "(from inline report text or a Bugzilla bug id) and return findings."
        ),
        job_name="hackbot-agent-autowebcompat-repro",
        input_schema=AutowebcompatReproInputs,
    ),
    "test-plan-generator": AgentSpec(
        name="test-plan-generator",
        description=(
            "Generate 10 concise Firefox QA test cases from feature details, "
            "run them in Firefox through DevTools MCP, and report pass/fail results."
        ),
        job_name="hackbot-agent-test-plan-generator",
        input_schema=TestPlanGeneratorInputs,
    ),
}
