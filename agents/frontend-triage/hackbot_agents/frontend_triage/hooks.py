"""Record-time limits on what this agent may write to a bug.

Once a run marks itself high-confidence its actions are applied to Bugzilla with no
human in between, and an action's params are model output. These hooks bound that:
one public comment on the bug being triaged, and nothing else.

They run at record time rather than at apply time for two reasons. The refusal
reaches the agent as a tool error it can correct in the same run, and the
out-of-bounds action never reaches ``summary.json``. ``ActionsRecorder`` runs hooks
before appending, so raising here aborts the recording (see
:data:`hackbot_runtime.actions.ActionHook`).

The action **type** needs no check, and there is no field-change hook.
``ENABLED_ACTION_TYPES`` filters the tools the actions server exposes, so this agent
has no way to record a ``bugzilla.update_bug``, a ``bugzilla.create_bug`` or a
Phabricator action in the first place -- ``severity`` is a suggestion in the comment
now, which is what left that tool with no caller.
"""

from __future__ import annotations

import re

from agent_tools.registry import ToolError
from hackbot_runtime.actions import ActionHook, ActionsRecorder

from .config import owners_for_path

# A line the model writes to declare its severity. Anchored to the line start so an
# ordinary mention -- quoting a reporter, or arguing why something is not S1 -- does
# not count as a second declaration.
_SEVERITY_DECLARATION = re.compile(r"^Suggested severity:", re.MULTILINE)


def severity_block_hook(action: dict) -> None:
    """Refuse a comment that declares its severity more than once.

    The prompt asks for one block at the end. Nothing stops the model from also
    writing the level into its analysis, and the reader would then have two
    declarations that can disagree. Absence is fine and deliberate -- a run with low
    severity confidence omits the block -- so this only fires on a second one.
    """
    text = (action.get("params") or {}).get("text")
    if isinstance(text, str) and len(_SEVERITY_DECLARATION.findall(text)) > 1:
        raise ToolError(
            "your comment declares a severity more than once; keep the single "
            "`Suggested severity:` block at the end and drop the other"
        )


# Source paths reach the comment as the Searchfox placeholder plus a repo-relative
# path (see `SEARCHFOX_LINKS_PROMPT`), which is why this hook has to run before
# `permalink_hook` rewrites them into URLs.
_CITED_PATH = re.compile(r"\{\{\s*searchfox\.permalink\s*\}\}/?(?P<path>[^)\s#]+)")


def cited_paths(text: str) -> list[str]:
    """Every repo-relative source path the comment links to."""
    return [m.group("path") for m in _CITED_PATH.finditer(text)]


def component_guidance_hook(loaded: set[str]) -> ActionHook:
    """Refuse a comment citing code owned by a component whose guidance was never loaded.

    The prompt carries the guidance for the component the bug was filed in. When the
    investigation lands somewhere else, nothing but this makes the agent go and read the
    guidance for where it landed.

    ``loaded`` is shared with the ``guidance`` MCP server, which adds to it as the agent
    loads components, so the retry after a refusal succeeds.

    A path no component owns passes: `gfx/` has nothing to load, and neither does ordinary
    desktop chrome, so refusing either would fail the run over something the agent cannot
    satisfy.
    """

    def hook(action: dict) -> None:
        text = (action.get("params") or {}).get("text")
        if not isinstance(text, str):
            return

        missing: dict[str, str] = {}
        for path in cited_paths(text):
            owners = owners_for_path(path)
            # Any owner will do. Three Android components share `mobile/android/`, and
            # refusing a Toolbar comment for citing a file its own team owns is noise.
            if owners and not any(o.key in loaded for o in owners):
                # Keyed by the first owner, which is the one to name in the message.
                # Co-owners are alternatives, not a list to work through, so offering a
                # joined string would have the agent pass it back as one component name.
                missing.setdefault(owners[0].key, path)

        if missing:
            named = ", ".join(
                f"{key} (e.g. {path})" for key, path in sorted(missing.items())
            )
            raise ToolError(
                f"your comment cites code owned by {named}, whose guidance you have not "
                f"read. Call `load_component_guidance` for each one named, then revise "
                f"the comment against what it says -- your localization may be wrong."
            )

    return hook


def _check_no_comment_yet(recorder: ActionsRecorder) -> None:
    # The rules ask for a single comment, but nothing else caps the count, and the
    # agent reads every comment on the bug as untrusted input. A run told to write
    # "a single brief comment" could record fifty.
    if any(action["type"] == "bugzilla.add_comment" for action in recorder.actions):
        raise ToolError(
            "you have already recorded a comment; record one per run, "
            "revising it rather than adding another"
        )


def _check_target_bug(params: dict, bug_id: int) -> None:
    if params.get("bug_id") != bug_id:
        raise ToolError(
            f"you are triaging bug {bug_id}; record actions against that bug, "
            f"not bug {params.get('bug_id')!r}"
        )


def add_comment_hook(recorder: ActionsRecorder, bug_id: int) -> ActionHook:
    """Refuse a ``bugzilla.add_comment`` this agent may not post.

    One public comment, on the bug being triaged. A private comment is invisible to
    the reporter and to everyone else on the bug, so nobody would see what an
    unattended run concluded — and the developers on the bug are the audience for it.
    """

    def hook(action: dict) -> None:
        params = action.get("params") or {}
        _check_no_comment_yet(recorder)
        _check_target_bug(params, bug_id)

        if params.get("is_private"):
            raise ToolError(
                "record the comment publicly: everyone on the bug needs to read it"
            )

    return hook
