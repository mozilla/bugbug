"""Authorization checks for GitHub webhook senders.

A GitHub user may trigger Hackbot on a repository only if they hold one of the
allowed roles (by default write, maintain or admin) on *that* repository. There
is no repository or organization allowlist: which repositories are reachable is
decided by where the GitHub App is installed.
"""

import logging
from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Any

from cachetools import TTLCache
from githubkit.exception import RequestFailed

from app.github import GitHubApp

log = logging.getLogger(__name__)

# GET /repos/{owner}/{repo}/collaborators/{username}/permission is listed under
# the "Metadata" repository permission (read) in GitHub's "Permissions required
# for GitHub Apps" reference. Every App has metadata:read, so this is the
# narrowest token _check_permissions accepts. If GitHub ever starts answering
# 403 here, this is the assumption to revisit.
COLLABORATOR_PERMISSION_TOKEN_PERMISSIONS: Mapping[str, str] = MappingProxyType(
    {"metadata": "read"}
)


def is_bot_sender(sender: Mapping[str, Any] | None, bot_login: str) -> bool:
    """Return whether a webhook ``sender`` must be ignored as a bot.

    Rejects GitHub's own ``Bot`` accounts, any ``*[bot]`` login and Hackbot's
    own bot login, so neither Hackbot nor another App can trigger a run. A
    sender without a login is treated as a bot too: when in doubt, ignore it.
    """
    if not sender:
        return True
    login = sender.get("login")
    if not isinstance(login, str) or not login:
        return True
    login = login.lower()
    return (
        sender.get("type") == "Bot"
        or login.endswith("[bot]")
        or login == bot_login.lower()
    )


class GitHubAuthorizer:
    """Cache-backed per-repository role checks for GitHub users.

    Each check uses the installation the webhook event came from, so the App
    can serve repositories in several organizations, each with its own
    installation.
    """

    def __init__(
        self,
        github_app: GitHubApp,
        allowed_roles: Iterable[str],
        *,
        cache_ttl_seconds: int = 300,
        cache_maxsize: int = 4096,
    ) -> None:
        self._github_app = github_app
        self._allowed_roles = frozenset(role.lower() for role in allowed_roles)
        self._cache: TTLCache[tuple[int, str, str, str], bool] = TTLCache(
            maxsize=cache_maxsize,
            ttl=cache_ttl_seconds,
        )

    async def is_authorized(
        self, installation_id: int, owner: str, repo: str, login: str
    ) -> bool:
        """Return whether ``login`` may trigger Hackbot on ``owner/repo``.

        ``installation_id``, ``owner`` and ``repo`` must come from the webhook
        payload, never from user-written text. Fails closed: an unknown user, a
        role outside the allowed set, or any error denies.
        """
        if login.lower().endswith("[bot]"):
            return False

        # GitHub logins, owners and repository names are case-insensitive.
        key = (installation_id, owner.lower(), repo.lower(), login.lower())
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        try:
            role = await self._get_role_name(installation_id, owner, repo, login)
        except RequestFailed as e:
            if e.response.status_code != 404:
                log.warning(
                    "GitHub permission check for %s on %s/%s failed with %s",
                    login,
                    owner,
                    repo,
                    e.response.status_code,
                )
                # Don't cache: a transient failure shouldn't lock a user out.
                return False
            # Not a collaborator, or no such user: a definitive answer.
            role = None
        except Exception:
            log.exception(
                "GitHub permission check for %s on %s/%s failed", login, owner, repo
            )
            return False

        authorized = role is not None and role.lower() in self._allowed_roles
        self._cache[key] = authorized
        return authorized

    async def _get_role_name(
        self, installation_id: int, owner: str, repo: str, login: str
    ) -> str:
        """The user's repository role, e.g. ``write`` or a custom role name.

        Uses ``role_name`` rather than the legacy ``permission`` field, which
        reports maintain as write and triage as read.
        """
        github = self._github_app.as_installation(
            installation_id,
            permissions=COLLABORATOR_PERMISSION_TOKEN_PERMISSIONS,
            repositories=[repo],
        )
        response = await github.rest.repos.async_get_collaborator_permission_level(
            owner, repo, login
        )
        return response.parsed_data.role_name
