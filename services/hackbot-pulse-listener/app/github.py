import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(30.0)


def _repo_slug() -> str:
    """``owner/repo`` parsed from the configured firefox git url."""
    return settings.firefox_git_url.rstrip("/").removeprefix("https://github.com/")


def commit_author_email(git_commit: str) -> str | None:
    """Author email of a firefox git commit, or None.

    The build-repair agent returns the commit it blamed for the failure; we look
    that commit up in the firefox GitHub mirror to notify its author directly.
    """
    url = f"https://api.github.com/repos/{_repo_slug()}/commits/{git_commit}"
    headers = {"Accept": "application/vnd.github+json"}
    try:
        resp = httpx.get(url, headers=headers, timeout=_TIMEOUT)
        resp.raise_for_status()
        author = (resp.json().get("commit") or {}).get("author") or {}
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Failed to fetch author for commit %s: %s", git_commit, exc)
        return None
    return author.get("email") or None
