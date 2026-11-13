"""Start a gecko-triage run: pick the team when the run input names none, then run.

`config` reads `TEAM` when it is imported, so the team is set before any module that
imports `config`.
"""

import asyncio
import os
import sys

from hackbot_runtime import run_async

from .team_pick import pick_team


def set_team() -> None:
    """Set `TEAM` from the bug's component when the run input names no team."""
    if os.environ.get("TEAM"):
        return
    team = asyncio.run(pick_team(os.environ["BROKER_URL"], int(os.environ["BUG_ID"])))
    print(f"[gecko_triage] team from the bug's component: {team}", file=sys.stderr)
    if team:
        os.environ["TEAM"] = team


if __name__ == "__main__":
    set_team()
    from .run import main

    run_async(main)
