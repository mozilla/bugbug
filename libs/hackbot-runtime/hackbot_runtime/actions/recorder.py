from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from hackbot_runtime.artifacts import publish_file
from hackbot_runtime.uploader import SignedPolicyUploader

# Callback run on an action of one type before it is recorded. It receives the
# action dict (``type``/``params``/``reasoning``, plus ``ref`` when given) and
# may mutate it in place; the return value is ignored. Raising aborts the
# recording (nothing is appended and no attachment is published), so a hook
# doubles as a validation gate: raise ``ToolError`` to report the reason back to
# the agent.
ActionHook = Callable[[dict], None]


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
        self._actions: list[dict] = []
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
        ref: str | None = None,
    ) -> dict:
        """Record an intended action.

        ``action_type`` uses ``<domain>.<verb>`` (e.g. ``bugzilla.update_bug``,
        ``phabricator.create_revision``). ``params`` is action-specific data
        the apply step will need. ``attachments`` maps a logical name to a
        local file path; each file is preserved under the stable key
        ``attachments/<action_index>/<name>``: uploaded via the runtime
        uploader when one is configured, otherwise copied into the local
        artifacts directory (so it is retrievable from compose/direct runs).
        The recorded action references it by that key; the original local
        path is not persisted (it disappears with the container).

        ``ref`` optionally labels this action so a *later* action in the same
        run can reference its apply-time result (e.g. a Bugzilla comment's
        text containing ``{{actions.patch.url}}`` after a
        ``phabricator.submit_patch`` action recorded with ``ref="patch"``).
        Resolved by the apply step, since the result doesn't exist yet at
        record time.

        Any hooks registered for ``action_type`` run on the action, in
        registration order, and may still change it (see :data:`ActionHook`). A
        hook that raises propagates to the caller and the action is not
        recorded. Hooks run before ``attachments`` are published, so an aborted
        recording leaves nothing behind: the action the hooks see carries no
        ``attachments`` key yet.
        """
        idx = len(self._actions)
        action: dict = {
            "type": action_type,
            "params": params,
            "reasoning": reasoning,
        }
        if ref is not None:
            action["ref"] = ref

        for hook in self._hooks.get(action_type, ()):
            hook(action)

        if attachments:
            recorded_attachments: list[dict] = []
            for name, path in attachments.items():
                key = publish_file(
                    self._uploader,
                    self._artifacts_dir,
                    f"attachments/{idx}/{name}",
                    path,
                )
                recorded_attachments.append({"name": name, "uploaded_key": key})
            action["attachments"] = recorded_attachments

        self._actions.append(action)
        return action

    @property
    def actions(self) -> list[dict]:
        return list(self._actions)
