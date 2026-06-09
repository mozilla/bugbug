"""Tests for the @tool decorator and signature-derived schema."""

from dataclasses import dataclass
from typing import Annotated

from agent_tools.registry import ToolError, tool, tool_name_for, tools_in
from pydantic import Field


@dataclass
class _Ctx:
    value: int


@tool
async def sample_tool(
    ctx: _Ctx,
    bug_id: Annotated[int, Field(description="The bug id.")],
    note: Annotated[str, Field(description="A note.")] = "x",
) -> dict:
    """Sample tool docstring."""
    return {"bug_id": bug_id, "note": note}


_DEFN = next(d for d in tools_in(__name__) if d.name == "sample_tool")


def test_decorator_infers_identity():
    assert _DEFN.name == "sample_tool"
    assert _DEFN.namespace == "test_registry"  # module basename
    assert _DEFN.description == "Sample tool docstring."
    assert _DEFN.dotted == "test_registry.sample_tool"


def test_schema_excludes_ctx_and_keeps_descriptions():
    schema = _DEFN.input_schema
    props = schema["properties"]
    assert "ctx" not in props
    assert set(props) == {"bug_id", "note"}
    assert props["bug_id"]["description"] == "The bug id."


def test_schema_marks_required_vs_optional():
    schema = _DEFN.input_schema
    assert "bug_id" in schema.get("required", [])
    assert "note" not in schema.get("required", [])  # has a default


def test_schema_is_cached():
    assert _DEFN.input_schema is _DEFN.input_schema


async def test_handler_remains_callable():
    out = await sample_tool(_Ctx(value=1), bug_id=7)
    assert out == {"bug_id": 7, "note": "x"}


def test_tool_name_for():
    assert tool_name_for("bugzilla.update_bug") == "bugzilla_update_bug"


def test_tool_error_carries_payload():
    err = ToolError("bad", payload={"error": "x"})
    assert err.payload == {"error": "x"}
