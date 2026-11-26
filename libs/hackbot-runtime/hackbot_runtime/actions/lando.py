"""Lando-domain recordable actions.

Agent code records a backout of a landed commit via :func:`record_backout`; the
apply side has Lando revert it on the landing repo through the headless API (see
``handlers/lando_handler.py``).
"""

from __future__ import annotations

from hackbot_runtime.actions.recorder import ActionsRecorder

BACKOUT_ACTION_TYPE = "lando.backout"


def record_backout(
    recorder: ActionsRecorder,
    *,
    lando_repo: str,
    commit: str,
    reasoning: str | None = None,
) -> dict:
    """Record a backout of ``commit`` (a full git sha) from ``lando_repo``.

    ``lando_repo`` is Lando's name for the repo, e.g. ``firefox-autoland``.
    """
    return recorder.record(
        BACKOUT_ACTION_TYPE,
        {"lando_repo": lando_repo, "commit": commit},
        reasoning=reasoning,
    )
