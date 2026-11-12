"""Authorization checks for GitHub webhook senders.

A GitHub user may trigger Hackbot on a repository only if they hold one of the
allowed roles (by default write, maintain or admin) on *that* repository. There
is no repository or organization allowlist: which repositories are reachable is
decided by where the GitHub App is installed.
"""

import asyncio
import logging
from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Any

from cachetools import TTLCache
from githubkit.exception import RequestFailed

from app.github import GitHubApp

log = logging.getLogger(__name__)

# GitHub abandons a webhook delivery that isn't answered within 10 seconds. A
# cold check mints a token and then fetches the role, one after the other, so
# bound the whole lookup and leave the handler time to respond.
DEFAULT_LOOKUP_TIMEOUT_SECONDS = 5.0

# GET /repos/{owner}/{repo}/collaborators/{username}/permission is listed under
# the "Metadata" repository permission (read) in GitHub's "Permissions required
# for GitHub Apps" reference. Every App has metadata:read, so this is the
# narrowest token _check_permissions accepts. If GitHub ever starts answering
# 403 here, this is the assumption to revisit.
COLLABORATOR_PERMISSION_TOKEN_PERMISSIONS: Mapping[str, str] = MappingProxyType(
    {"metadata": "read"}
)

# The login GitHub shows in place of a deleted account.
DELETED_USER_LOGIN = "ghost"


def is_bot_sender(sender: Mapping[str, Any] | None, bot_login: str) -> bool:
    """Return whether a webhook ``sender`` must be ignored as a bot.

    Rejects a missing sender, GitHub's own ``Bot`` accounts, any ``*[bot]``
    login and Hackbot's own bot login, so neither Hackbot nor another App can
    trigger a run.
    """
    if not sender or sender.get("type") == "Bot":
        return True
    login = sender["login"]
    return login.endswith("[bot]") or login == bot_login


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
        bot_login: str,
        *,
        cache_ttl_seconds: int = 300,
        cache_maxsize: int = 4096,
        lookup_timeout_seconds: float = DEFAULT_LOOKUP_TIMEOUT_SECONDS,
    ) -> None:
        self._github_app = github_app
        self._lookup_timeout_seconds = lookup_timeout_seconds
        self._allowed_roles = frozenset(allowed_roles)
        self._bot_login = bot_login
        self._cache: TTLCache[tuple[int, str, str, str], bool] = TTLCache(
            maxsize=cache_maxsize,
            ttl=cache_ttl_seconds,
        )

    async def is_authorized(
        self,
        installation_id: int,
        owner: str,
        repo: str,
        sender: Mapping[str, Any] | None,
    ) -> bool:
        """Return whether the webhook ``sender`` may trigger Hackbot on ``owner/repo``.

        Every argument must come from the webhook payload, never from
        user-written text. Fails closed: a bot, a deleted user, an unknown user,
        a role outside the allowed set, any error, or a lookup that outlasts
        ``lookup_timeout_seconds`` denies.
        """
        if is_bot_sender(sender, self._bot_login):
            return False
        login = sender["login"]
        if login == DELETED_USER_LOGIN:
            return False

        key = (installation_id, owner, repo, login)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        try:
            async with asyncio.timeout(self._lookup_timeout_seconds):
                role = await self._get_role_name(installation_id, owner, repo, login)
        except TimeoutError:
            log.warning(
                "GitHub permission check for %s on %s/%s timed out after %ss",
                login,
                owner,
                repo,
                self._lookup_timeout_seconds,
            )
            # Don't cache: a slow GitHub shouldn't lock a user out.
            return False
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

        authorized = role in self._allowed_roles
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
