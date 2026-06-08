import functools
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic import create_model


class ActionInputError(Exception):
    """Invalid action input (bad path, etc.).

    Raised by handlers; a per-framework adapter turns it into the
    framework's tool-error signal. The action layer imports no framework
    error type.
    """


@dataclass
class ActionDefinition:
    """Declarative description of one recordable action.

    ``handler`` is an async function whose **first positional parameter** is
    the ``ActionsRecorder``. The remaining parameters carry typed
    annotations (``Annotated[T, Field(...)]``) that double as the
    agent-facing schema, exposed framework-neutrally via ``input_schema``.
    Handlers return a short confirmation string.
    """

    type: str
    description: str
    handler: Callable[..., Awaitable[str]]

    @functools.cached_property
    def input_schema(self) -> dict:
        """JSON schema of the agent-facing arguments (excludes ``recorder``).

        Derived once from the handler signature so every adapter (MCP today,
        LangChain later) shares one schema.
        """
        sig = inspect.signature(self.handler, eval_str=True)
        fields = {
            name: (
                param.annotation,
                ... if param.default is inspect.Parameter.empty else param.default,
            )
            for name, param in list(sig.parameters.items())[1:]  # skip `recorder`
        }
        model = create_model(self.type.replace(".", "_") + "_args", **fields)
        return model.model_json_schema()


def get_actions(types: list[str] | None = None) -> list[ActionDefinition]:
    """Return registered actions, optionally filtered by ``type`` list.

    Import is deferred to avoid an import cycle between the registry and
    the per-domain modules that register actions.
    """
    from hackbot_runtime.actions import ALL_ACTIONS

    if types is None:
        return list(ALL_ACTIONS)
    wanted = set(types)
    return [a for a in ALL_ACTIONS if a.type in wanted]
