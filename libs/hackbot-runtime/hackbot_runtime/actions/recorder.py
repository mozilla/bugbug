from pathlib import Path

from hackbot_runtime.artifacts import publish_file
from hackbot_runtime.uploader import SignedPolicyUploader


class ActionsRecorder:
    """Collects structured actions an agent decided to take.

    The runtime serialises the collected list into the
    ``actions`` array of ``summary.json``; a downstream apply step picks
    them up from there.

    Framework-agnostic: knows nothing about MCP, LangChain, or any specific
    action domain. Per-framework adapters wrap this and translate their
    native tool calls into ``record(...)`` calls.
    """

    def __init__(
        self,
        uploader: SignedPolicyUploader | None = None,
        artifacts_dir: Path | None = None,
    ) -> None:
        self._actions: list[dict] = []
        self._uploader = uploader
        self._artifacts_dir = artifacts_dir
        # Optional per-run needinfo target for the bugzilla comment footer. Left
        # None by default so callers that don't set it get the generic footer.
        self.needinfo_target: str | None = None

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
        """
        idx = len(self._actions)
        action: dict = {
            "type": action_type,
            "params": params,
            "reasoning": reasoning,
        }
        if ref is not None:
            action["ref"] = ref

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
