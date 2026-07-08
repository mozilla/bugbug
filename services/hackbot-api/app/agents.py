from dataclasses import dataclass

from pydantic import BaseModel

from app.schemas import (
    AutowebcompatReproInputs,
    BugFixInputs,
    BuildRepairInputs,
    FrontendTriageInputs,
    TestPlanGeneratorInputs,
)


@dataclass(frozen=True)
class AgentSpec:
    name: str
    description: str
    job_name: str
    input_schema: type[BaseModel]


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
    "build-repair": AgentSpec(
        name="build-repair",
        description="Analyze a Firefox build failure at a specific commit and produce a candidate fix patch.",
        job_name="hackbot-agent-build-repair",
        input_schema=BuildRepairInputs,
    ),
    "frontend-triage": AgentSpec(
        name="frontend-triage",
        description="Triage a Firefox desktop frontend bug (read-only) and produce a root-cause analysis and proposed fix plan.",
        job_name="hackbot-agent-frontend-triage",
        input_schema=FrontendTriageInputs,
    ),
    "test-plan-generator": AgentSpec(
        name="test-plan-generator",
        description=(
            "Generate Firefox QA test cases from feature details (up to 20 test cases), "
            "run them in Firefox through DevTools MCP, and report pass/fail/unsuitable results."
        ),
        job_name="hackbot-agent-test-plan-generator",
        input_schema=TestPlanGeneratorInputs,
    ),
}
