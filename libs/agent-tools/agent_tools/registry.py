"""Framework-neutral declaration of agent tools.

A ``@tool``-decorated handler is the single source of truth for one agent tool:
its name (the function name), namespace (the defining module's basename),
description (the docstring) and argument schema (the typed signature, minus the
first ``ctx`` parameter). Per-framework adapters (claude-agent-sdk today,
LangChain later) consume :class:`ToolDefinition` without the handlers importing
any framework. This module imports no agent framework — only pydantic.
"""

from __future__ import annotations

import functools
import inspect
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic import create_model

ACTIONS_SERVER_NAME = "actions"


def tool_name_for(dotted: str) -> str:
    """Map a dotted tool id to its MCP tool name: ``bugzilla.update_bug`` -> ``bugzilla_update_bug``."""
    return dotted.replace(".", "_")


class ToolError(Exception):
    """An agent tool failed in an expected way.

    Raised by handlers; a per-framework adapter renders it as that framework's
    tool-error signal. The optional ``payload`` carries a structured error body
    (preferred over a bare message when the agent benefits from machine-readable
    detail). The tool layer imports no framework error type.
    """

    def __init__(self, message: str, *, payload: dict | None = None) -> None:
        super().__init__(message)
        self.payload = payload


@dataclass
class ToolDefinition:
    """Declarative description of one agent tool, derived from a handler.

    ``handler`` is an async function whose **first positional parameter** is the
    tool context (e.g. a ``BugzillaContext`` or an actions recorder); the
    remaining parameters carry ``Annotated[T, Field(...)]`` annotations that
    define the agent-facing schema.
    """

    name: str
    namespace: str
    description: str
    handler: Callable[..., Awaitable]

    @property
    def dotted(self) -> str:
        return f"{self.namespace}.{self.name}"

    @functools.cached_property
    def args_model(self):
        """Pydantic model of the agent-facing args (excludes the ``ctx`` param).

        Derived once from the handler signature so every adapter shares one
        schema — claude-agent-sdk consumes ``input_schema``; a LangChain adapter
        can use this model directly as ``args_schema``.
        """
        sig = inspect.signature(self.handler, eval_str=True)
        fields = {
            name: (
                param.annotation,
                ... if param.default is inspect.Parameter.empty else param.default,
            )
            for name, param in list(sig.parameters.items())[1:]  # skip `ctx`
        }
        return create_model(f"{self.namespace}_{self.name}_args", **fields)

    @functools.cached_property
    def input_schema(self) -> dict:
        return self.args_model.model_json_schema()


_REGISTRY: dict[str, list[ToolDefinition]] = defaultdict(list)


def tool(fn: Callable[..., Awaitable]) -> Callable[..., Awaitable]:
    """Register ``fn`` as a tool, inferring name/namespace/description.

    name = function name; namespace = defining module's basename; description =
    function docstring. The function is returned unchanged (still callable);
    collect a module's tools with :func:`tools_in`.
    """
    namespace = fn.__module__.rsplit(".", 1)[-1]
    _REGISTRY[fn.__module__].append(
        ToolDefinition(
            name=fn.__name__,
            namespace=namespace,
            description=inspect.getdoc(fn) or "",
            handler=fn,
        )
    )
    return fn


def tools_in(module_name: str) -> list[ToolDefinition]:
    """Return the tools registered by ``@tool`` in the given module (``__name__``)."""
    return list(_REGISTRY[module_name])
