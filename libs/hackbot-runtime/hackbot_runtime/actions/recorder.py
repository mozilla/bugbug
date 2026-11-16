import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from agent_tools.registry import ToolError
from pydantic import BaseModel

from hackbot_runtime.artifacts import publish_file
from hackbot_runtime.uploader import SignedPolicyUploader


class RecordedAttachment(BaseModel):
    """A file attached to an action, stored under ``uploaded_key``."""

    name: str
    uploaded_key: str


class RecordedAction(BaseModel):
    """An action an agent decided to take, collected by :class:`ActionsRecorder`.

    Serialised with :meth:`model_dump` into the ``actions`` array of
    ``summary.json``.
    """

    action_id: str
    type: str
    params: dict[str, Any]
    reasoning: str | None = None
    attachments: list[RecordedAttachment] = []

    def confirmation(self) -> str:
        """Return the agent-facing confirmation for this recorded action."""
        return f"Recorded {self.type} (ID: {self.action_id})."


# Callback run on an action of one type before it is recorded. It receives the
# :class:`RecordedAction` and may mutate it in place; the return value is
# ignored. Raising aborts the recording (nothing is appended and no attachment is
# published), so a hook doubles as a validation gate: raise ``ToolError`` to
# report the reason back to the agent.
ActionHook = Callable[[RecordedAction], None]


class ActionsRecorder:
    """Collects structured actions an agent decided to take.

    The runtime serialises the collected list into the
    ``actions`` array of ``summary.json``; a downstream apply step picks
    them up from there.

    Framework-agnostic: knows nothing about MCP, LangChain, or any specific
    action domain. Per-framework adapters wrap this and translate their
    native tool calls into ``record(...)`` calls.

    ``hooks`` maps an action type to the :data:`ActionHook` callbacks to run,
    in order, before that action is appended. They live here rather than in the
    action declarations so the runtime can attach behaviour that cuts across
    domains (enrichment, validation) without every ``@tool`` handler knowing
    about it.
    """

    def __init__(
        self,
        uploader: SignedPolicyUploader | None = None,
        artifacts_dir: Path | None = None,
        hooks: Mapping[str, Sequence[ActionHook]] = {},
    ) -> None:
        self._actions: dict[str, RecordedAction] = {}
        self._uploader = uploader
        self._artifacts_dir = artifacts_dir
        self._hooks = {
            action_type: list(action_hooks)
            for action_type, action_hooks in hooks.items()
        }

    def add_hook(self, action_type: str, hook: ActionHook) -> None:
        """Append ``hook`` to the hooks run for ``action_type``.

        For wiring a hook after construction; it runs after any hook already
        registered for that type.
        """
        self._hooks.setdefault(action_type, []).append(hook)

    def record(
        self,
        action_type: str,
        params: dict,
        *,
        reasoning: str | None = None,
        attachments: dict[str, Path] | None = None,
    ) -> RecordedAction:
        """Record an action and return a detached copy with its ID.

        ``action_type`` uses ``<domain>.<verb>`` (e.g. ``bugzilla.update_bug``,
        ``phabricator.create_revision``). ``params`` is action-specific data
        the apply step will need. ``attachments`` maps a logical name to a
        local file path; each file is preserved under the stable key
        ``attachments/<action_id>/<name>``: uploaded via the runtime
        uploader when one is configured, otherwise copied into the local
        artifacts directory (so it is retrievable from compose/direct runs).
        The recorded action references it by that key; the original local path
        is not persisted (it disappears with the container).

        ``params`` may reference an earlier action's apply-time result by its
        action ID (e.g. a Bugzilla comment's text containing
        ``{{actions.<action_id>.url}}`` after a ``phabricator.submit_patch``
        action). Resolved by the apply step, since the result doesn't exist yet
        at record time.

        Any hooks registered for ``action_type`` run on the action, in
        registration order, and may still change it (see :data:`ActionHook`). A
        hook that raises propagates to the caller and the action is not
        recorded. Hooks run before ``attachments`` are published, so an aborted
        recording leaves nothing behind: the action the hooks see carries no
        ``attachments`` yet.
        """
        action_id = uuid.uuid4().hex
        action = RecordedAction(
            action_id=action_id,
            type=action_type,
            params=params,
            reasoning=reasoning,
        )

        for hook in self._hooks.get(action_type, ()):
            hook(action)

        if attachments:
            action.attachments = [
                RecordedAttachment(
                    name=name,
                    uploaded_key=publish_file(
                        self._uploader,
                        self._artifacts_dir,
                        f"attachments/{action_id}/{name}",
                        path,
                    ),
                )
                for name, path in attachments.items()
            ]

        self._actions[action_id] = action
        return action.model_copy(deep=True)

    def list_actions(self) -> list[RecordedAction]:
        """Return detached copies of the current actions with stable in-run IDs."""
        return [action.model_copy(deep=True) for action in self._actions.values()]

    def remove_action(self, action_id: str) -> RecordedAction:
        """Remove the action with the given ID and return it."""
        action = self._actions.get(action_id)
        if action is None:
            raise ToolError(f"No recorded action with ID {action_id!r}.")

        del self._actions[action_id]
        return action

    @property
    def actions(self) -> list[RecordedAction]:
        return list(self._actions.values())
