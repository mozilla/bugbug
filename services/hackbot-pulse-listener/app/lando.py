import logging

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(30.0)


def _map(direction: str, rev: str, key: str) -> str | None:
    url = f"https://lando.moz.tools/api/{direction}/firefox/{rev}"
    try:
        resp = httpx.get(url, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()[key]
    except (httpx.HTTPError, KeyError) as exc:
        logger.debug("Failed to map revision %s via %s: %s", rev, direction, exc)
        return None


def hg_to_git(hg_rev: str) -> str | None:
    """Resolve a Mercurial revision to the matching firefox git SHA, or None."""
    return _map("hg2git", hg_rev, "git_hash")


def git_to_hg(git_rev: str) -> str | None:
    """Resolve a firefox git SHA to the matching Mercurial revision, or None."""
    return _map("git2hg", git_rev, "hg_hash")
