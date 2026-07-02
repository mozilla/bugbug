import logging

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(30.0)


def hg_to_git(hg_rev: str) -> str | None:
    """Resolve a Mercurial revision to the matching firefox git SHA, or None."""
    url = f"https://lando.moz.tools/api/hg2git/firefox/{hg_rev}"
    try:
        resp = httpx.get(url, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()["git_hash"]
    except (httpx.HTTPError, KeyError) as exc:
        logger.debug("Failed to map hg revision %s to git: %s", hg_rev, exc)
        return None
