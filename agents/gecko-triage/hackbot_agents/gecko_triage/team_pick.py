"""The team that triages a bug, when the run input names none.

This runs before `config` reads `TEAM`, so it must not import `config`.
"""

import sys
from pathlib import Path

from .broker_client import broker_session, mcp_url
from .teamconfig import TEAMS_DIR, load_team, team_dir

# Seconds for the one Bugzilla read. A run that cannot read the bug fails later.
PICK_TIMEOUT = 60


def team_for(
    product: object, component: object, teams_dir: Path = TEAMS_DIR
) -> str | None:
    """The one team whose components include ``product :: component``, or None.

    A team directory that `team_dir` refuses is left out, as `config` would refuse it.
    """
    key = f"{product} :: {component}"
    matches = []
    for path in sorted(teams_dir.glob("*/team.toml")):
        try:
            root = team_dir(teams_dir, path.parent.name)
        except ValueError:
            continue
        if key in {c.key for c in load_team(root / "team.toml").components}:
            matches.append(root.name)
    return matches[0] if len(matches) == 1 else None


async def pick_team(broker_url: str, bug: int) -> str | None:
    """The team for ``bug``, from its product and component.

    None when no team, or more than one, triages that component, and when the bug
    cannot be read.
    """
    try:
        async with broker_session(mcp_url(broker_url), PICK_TIMEOUT) as call:
            reply = await call(
                "get_bugs", {"ids": [bug], "include_fields": ["product", "component"]}
            )
    except Exception as e:  # The state read reports the failure later.
        print(
            f"[gecko_triage] cannot read bug {bug} to pick a team: {e}", file=sys.stderr
        )
        return None
    bugs = reply.get("bugs") or []
    return team_for(bugs[0].get("product"), bugs[0].get("component")) if bugs else None
