"""
Demonstrates the schema compilation limit hit by the code review agent.

Sentry issue: REVIEWHELPER-API-Z
GitHub issue: https://github.com/mozilla/bugbug/issues/6109

Three factors combine to exceed Anthropic's compilation limit:
  1. thinking (adaptive or enabled)
  2. output_config — sent by langchain_anthropic when ProviderStrategy is used;
     carries the full AgentResponse/GeneratedReviewComment JSON schema
  3. 12 tool schemas — 11 SEARCHFOX tools + write_todos injected by TodoListMiddleware

Any two of these three alone is fine. All three together triggers:
  "Schema is too complex for compilation. Try reducing the number of tools
   or simplifying tool schemas."

Run with:
  ANTHROPIC_API_KEY=... uv run --package bugbug python3 _schema_test.py
"""
import warnings
warnings.filterwarnings("ignore")

import anthropic
from langchain_anthropic.chat_models import convert_to_anthropic_tool, _convert_to_anthropic_output_config_format
from langchain.agents.structured_output import ProviderStrategy, OutputToolBinding, _SchemaSpec
from langchain_core.tools import StructuredTool
from pydantic import BaseModel
from typing import Literal
from typing_extensions import TypedDict

from bugbug.tools.code_review.langchain_tools import SEARCHFOX_TOOLS
from bugbug.tools.code_review.data_types import AgentResponse


# ---------------------------------------------------------------------------
# Replicate the tool list that create_agent() receives at runtime.
#
# SEARCHFOX_TOOLS already has _make_non_strict() applied to expand_context
# and find_definition (PR #6129). The provider_tool_definition extras bypass
# strict=True in bind_tools — those two are sent without additionalProperties:false.
#
# write_todos is injected by TodoListMiddleware and arrives as a 12th tool.
# ---------------------------------------------------------------------------

class Todo(TypedDict):
    content: str
    status: Literal["pending", "in_progress", "completed"]

class WriteTodosInput(BaseModel):
    todos: list[Todo]

write_todos_tool = StructuredTool.from_function(
    name="write_todos",
    description="Manage todo list",
    func=lambda todos: "ok",
    args_schema=WriteTodosInput,
    infer_schema=False,
)

tools = []
for t in list(SEARCHFOX_TOOLS) + [write_todos_tool]:
    extras = getattr(t, "extras", {}) or {}
    if "provider_tool_definition" in extras:
        tools.append(extras["provider_tool_definition"])
    else:
        tools.append(convert_to_anthropic_tool(t, strict=True))

# ---------------------------------------------------------------------------
# ProviderStrategy sends AgentResponse as output_config.format (native
# structured output). langchain_anthropic converts the OpenAI-style
# response_format kwarg into Anthropic's output_config when it sees it in
# the payload — introduced in langchain-anthropic 1.3.3 (Feb 2026).
# ---------------------------------------------------------------------------
output_schema = ProviderStrategy(AgentResponse).to_model_kwargs()["response_format"]["json_schema"]["schema"]
output_config = {"format": _convert_to_anthropic_output_config_format(output_schema)}

# ToolStrategy alternative: AgentResponse becomes a regular tool call.
# The model is asked to call it rather than having the API enforce the schema.
agent_response_tool = convert_to_anthropic_tool(
    OutputToolBinding.from_schema_spec(_SchemaSpec(AgentResponse)).tool,
    strict=True,
)

thinking = {"type": "adaptive"}

strict_count = sum(1 for t in tools if t.get("strict"))
print(f"Tool set: {len(tools)} tools, {strict_count} strict\n")

client = anthropic.Anthropic()

def check(label, **kwargs):
    try:
        client.messages.create(
            model="claude-opus-4-6",
            max_tokens=50,
            messages=[{"role": "user", "content": "hi"}],
            **kwargs,
        )
        print(f"  OK    {label}")
    except anthropic.BadRequestError as e:
        tag = "schema too complex" if "too complex" in str(e) else str(e)[:60]
        print(f"  FAIL  {label}  ({tag})")


# ---------------------------------------------------------------------------
# 1. Current broken state
# ---------------------------------------------------------------------------
print("1. Current state — all three factors present:")
check(
    "tools + output_config + thinking",
    tools=tools, output_config=output_config, thinking=thinking,
)

# ---------------------------------------------------------------------------
# 2. Remove thinking
#
# Passes, but thinking is what makes the model reason carefully about whether
# a comment is worth filing. Disabling it is a quality regression.
# ---------------------------------------------------------------------------
print("\n2. Remove thinking — passes, but degrades review quality:")
check(
    "tools + output_config, no thinking",
    tools=tools, output_config=output_config,
)

# ---------------------------------------------------------------------------
# 3. ToolStrategy instead of ProviderStrategy
#
# AgentResponse becomes an ordinary tool call: the model is asked to call it
# with the right shape rather than the API enforcing the schema server-side.
# Passes because there is no output_config in the request.
#
# Tradeoff: loses native structured-output enforcement. In practice Claude
# follows tool schemas reliably, but a malformed response surfaces as a
# parsing error in the app rather than being caught at the API boundary.
# Application-level Pydantic validation + retry can recover most of the
# robustness.
# ---------------------------------------------------------------------------
print("\n3. ToolStrategy — AgentResponse as a tool call, no output_config:")
check(
    "tools + agent_response_tool + thinking, no output_config",
    tools=tools + [agent_response_tool], thinking=thinking,
)

# ---------------------------------------------------------------------------
# 4. Why _make_non_strict (PR #6129) does not fix this
#
# The strict flag controls validation behaviour at the API boundary, but
# Anthropic compiles ALL tool schemas — strict and non-strict alike — when
# output_config + thinking are present. Making a tool non-strict does not
# remove it from the compilation budget; it only drops the
# additionalProperties:false requirement and the anyOf-in-strict-tools count.
#
# PR #6129 made expand_context and find_definition non-strict to stay under
# the 16-anyOf-in-strict-tools limit (a separate, older restriction). It
# happens to leave the tool schemas identical in size, so the compilation
# limit is hit regardless.
# ---------------------------------------------------------------------------
print("\n4. _make_non_strict on 2 tools (PR #6129) — still fails:")
print("   Strict flag only gates validation; all schemas are still compiled.")
check(
    "12 tools (2 non-strict + 10 strict) + output_config + thinking",
    tools=tools, output_config=output_config, thinking=thinking,
)
