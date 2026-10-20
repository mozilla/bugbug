"""Lando-domain recordable actions.

Agent code records a backout of a landed commit via :func:`record_backout`; the
apply side pushes the revert to the landing repo through Lando's headless API, as
``lando push-commits`` does (see ``handlers/lando_handler.py``).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from hackbot_runtime import changes
from hackbot_runtime.actions.recorder import ActionsRecorder

BACKOUT_ACTION_TYPE = "lando.backout"
BACKOUT_ATTACHMENT = "backout.patch"


def record_backout(
    recorder: ActionsRecorder,
    source_repo: Path,
    *,
    lando_repo: str,
    commit: str,
    reason: str,
    reasoning: str | None = None,
) -> dict:
    """Record a backout of ``commit`` from ``lando_repo`` (e.g. ``firefox-autoland``).

    ``reason`` completes the commit message ``Revert "<subject>" for causing
    <reason>.``, e.g. ``failures at test_foo.js``.
    """
    patch = changes.build_backout(source_repo, commit, reason)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / BACKOUT_ATTACHMENT
        path.write_bytes(patch)
        return recorder.record(
            BACKOUT_ACTION_TYPE,
            {"lando_repo": lando_repo, "commit": commit, "reason": reason},
            reasoning=reasoning,
            attachments={BACKOUT_ATTACHMENT: path},
        )
