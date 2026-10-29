"""GitHub App authentication on top of githubkit.

githubkit signs the App JWT and mints, caches and refreshes installation tokens
for the clients returned here. :meth:`GitHubApp.installation_token` is for
callers that need the raw token instead, e.g. ``git push`` or a subprocess.
"""

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any, cast

from githubkit import AppAuthStrategy, AppInstallationAuthStrategy, GitHub
from githubkit.cache import MemCacheStrategy
from githubkit.utils import UNSET
from githubkit_schemas.latest.types import AppPermissionsType

from app.config import GitHubAppSettings

# Requesting a permission the App wasn't granted fails the mint with a 422, so
# keep these within what the App is configured with. Read-only, so no caller can
# widen them for the whole process.
READ_ONLY_PERMISSIONS: Mapping[str, str] = MappingProxyType(
    {"contents": "read", "pull_requests": "read"}
)
READ_WRITE_PERMISSIONS: Mapping[str, str] = MappingProxyType(
    {"contents": "write", "pull_requests": "write"}
)

_PERMISSION_NAMES = (
    AppPermissionsType.__required_keys__ | AppPermissionsType.__optional_keys__
)


@dataclass(frozen=True)
class InstallationToken:
    token: str = field(repr=False)
    expires_at: datetime


class GitHubApp:
    """Authenticates as a GitHub App and its installations.

    Extra keyword arguments are passed through to :class:`githubkit.GitHub`
    (e.g. ``base_url``, ``timeout``).
    """

    def __init__(
        self,
        settings: GitHubAppSettings,
        *,
        refresh_margin: timedelta = timedelta(minutes=5),
        **github_options: Any,
    ):
        self._settings = settings
        self._auth = AppAuthStrategy(
            settings.app_id, settings.private_key.get_secret_value()
        )
        # Our own cache rather than githubkit's process-wide default, so tokens
        # are never shared between GitHubApp instances.
        self._github_options = {"cache_strategy": MemCacheStrategy(), **github_options}
        self._refresh_margin = refresh_margin
        self._tokens: dict[tuple, InstallationToken] = {}
        self._lock = asyncio.Lock()

    def as_app(self) -> GitHub[AppAuthStrategy]:
        """A client authenticated as the App itself, for the ``/app`` endpoints."""
        return GitHub(self._auth, **self._github_options)

    def as_installation(
        self,
        installation_id: int | None = None,
        *,
        permissions: Mapping[str, str],
        repositories: Sequence[str] | None = None,
    ) -> GitHub[AppInstallationAuthStrategy]:
        """A client authenticated as an installation, refreshing its own token.

        ``permissions`` and ``repositories`` (names, without the owner) narrow
        the token below what the installation was granted.
        """
        auth = self._auth.as_installation(
            self._resolve_installation_id(installation_id),
            repositories=UNSET if repositories is None else list(repositories),
            permissions=_check_permissions(permissions),
        )
        return GitHub(auth, **self._github_options)

    async def installation_token(
        self,
        installation_id: int | None = None,
        *,
        permissions: Mapping[str, str],
        repositories: Sequence[str] | None = None,
    ) -> InstallationToken:
        """A raw installation token, reused until it is close to expiring."""
        installation_id = self._resolve_installation_id(installation_id)
        permissions = _check_permissions(permissions)
        key = (
            installation_id,
            tuple(sorted(permissions.items())),
            None if repositories is None else tuple(sorted(repositories)),
        )
        async with self._lock:
            cached = self._tokens.get(key)
            if cached and cached.expires_at - datetime.now(UTC) > self._refresh_margin:
                return cached

            # githubkit's ``rest`` namespace only holds a weak reference to its
            # client, so the client must outlive the call.
            github = self.as_app()
            response = await github.rest.apps.async_create_installation_access_token(
                installation_id,
                permissions=permissions,
                repositories=UNSET if repositories is None else list(repositories),
            )
            minted = InstallationToken(
                token=response.parsed_data.token,
                expires_at=datetime.fromisoformat(response.parsed_data.expires_at),
            )
            self._tokens[key] = minted
            return minted

    def _resolve_installation_id(self, installation_id: int | None) -> int:
        installation_id = installation_id or self._settings.installation_id
        if installation_id is None:
            raise ValueError(
                "No GitHub App installation ID: pass one or set GITHUB_INSTALLATION_ID."
            )
        return installation_id


def _check_permissions(permissions: Mapping[str, str]) -> AppPermissionsType:
    # A token minted without permissions gets everything the App has, and
    # githubkit silently drops names it doesn't know, so an empty set or a typo
    # could widen the token instead of narrowing it.
    if not permissions:
        raise ValueError("permissions must name at least one permission")
    if unknown := permissions.keys() - _PERMISSION_NAMES:
        raise ValueError(f"Unknown GitHub App permissions: {sorted(unknown)}")
    return cast(AppPermissionsType, dict(permissions))
