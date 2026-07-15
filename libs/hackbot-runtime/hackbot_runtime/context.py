"""The single object an agent's ``main()`` receives from the runtime.

``HackbotContext`` is what an agent author touches. It answers for everything
the platform provides — the prepared source checkout, Firefox build paths,
model-provider credentials — plus the results/artifacts/actions plumbing, so the
author never cares how or from where those come.

Its platform fields are read from the environment (the orchestrator sets them);
its capability declarations come from the agent's ``hackbot.toml``
(:class:`HackbotConfig`), attached via :meth:`from_config`.
"""

from __future__ import annotations

import datetime
import logging
import os
import tempfile
import uuid
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import Field, PrivateAttr
from pydantic_settings import BaseSettings, SettingsConfigDict

from hackbot_runtime import artifacts, changes
from hackbot_runtime.actions.recorder import ActionsRecorder
from hackbot_runtime.config import HackbotConfig, load_config
from hackbot_runtime.providers import AnthropicAuth
from hackbot_runtime.source import ensure_source_repo
from hackbot_runtime.uploader import SignedPolicyUploader

if TYPE_CHECKING:
    from agent_tools.firefox import FirefoxContext

log = logging.getLogger("hackbot_runtime.context")


def _default_run_id() -> str:
    """A unique, sortable id for runs that don't get one from the platform.

    The orchestrator sets ``RUN_ID`` explicitly in production; this fallback
    keeps local/compose/direct runs unique (so per-run artifact dirs don't
    collide). The ``local-`` prefix marks it as not platform-assigned.
    """
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"local-{stamp}-{uuid.uuid4().hex[:6]}"


class HackbotContext(BaseSettings):
    """Platform capabilities + results plumbing handed to every agent's main().

    `run_id` defaults to a generated unique id (the orchestrator overrides it via
    ``RUN_ID`` in production). The results-upload fields are optional so local-dev
    runs (compose, scripts) can start the agent without a signed POST policy — in
    that case results are written into the local artifacts dir rather than
    uploaded.
    """

    run_id: str = Field(default_factory=_default_run_id)
    results_prefix: str = ""
    results_policy_url: str | None = None
    results_policy_fields: dict[str, str] = {}
    # Base for locally-persisted artifacts when no uploader is configured
    # (compose/direct runs). Each run is namespaced under it by run_id (see
    # `run_artifacts_dir`). Overridable via ARTIFACTS_DIR — compose points this
    # at a host-mounted dir; the default stays relative so it does not bake a
    # host path into the container.
    artifacts_dir: Path = Path("artifacts")

    model_config = SettingsConfigDict(extra="ignore")

    # Capability declarations from hackbot.toml (not env); attached after
    # construction via from_config()/from_config_obj().
    _config: HackbotConfig = PrivateAttr(default_factory=HackbotConfig)
    # The commit the source checkout started from, recorded when source_repo is
    # prepared. Stays None for agents that never touch source, which is how
    # publish_changes() knows there are no changes to collect.
    _source_base: str | None = PrivateAttr(default=None)

    @classmethod
    def from_config(cls, config_path: Path) -> "HackbotContext":
        """Build from ``hackbot.toml`` at ``config_path`` plus env-derived fields."""
        return cls.from_config_obj(load_config(config_path))

    @classmethod
    def from_config_obj(cls, config: HackbotConfig) -> "HackbotContext":
        """Build from an already-parsed config plus env-derived fields."""
        obj = cls()
        obj._config = config
        return obj

    @property
    def config(self) -> HackbotConfig:
        return self._config

    # --- Platform capabilities (declared in hackbot.toml) ------------- #

    @cached_property
    def source_repo(self) -> Path:
        """The prepared source checkout, cloned/refreshed on first access.

        The path comes from ``SOURCE_REPO`` (set by the orchestrator) or, failing
        that, the ``[source].checkout_path`` in ``hackbot.toml``. The checkout is
        prepared lazily so agents that never touch source pay no git cost.
        """
        if self._config.source is None:
            raise RuntimeError(
                "This agent did not declare a [source] in hackbot.toml; "
                "no source repository is available."
            )
        env_path = os.environ.get("SOURCE_REPO")
        path = Path(env_path) if env_path else self._config.source.checkout_path
        ref = os.environ.get("SOURCE_REF") or self._config.source.ref
        depth_env = os.environ.get("SOURCE_DEPTH")
        depth = int(depth_env) if depth_env else None
        ensure_source_repo(path, self._config.source.repo_url, ref, depth)
        # Record where the agent starts editing, so publish_changes() can later
        # diff the final tree against it. Best-effort: a failure here must not
        # break the agent's access to source — it only disables change capture.
        try:
            self._source_base = changes.base_commit(path)
        except Exception:
            log.warning("Could not record source base commit at %s", path)
        return path

    @cached_property
    def firefox(self) -> "FirefoxContext":
        """Firefox build paths derived from the prepared source checkout.

        Importing ``agent_tools.firefox`` lazily keeps the base runtime free of
        the ``agent-tools[firefox]`` extra for agents that don't need it.
        """
        if self._config.firefox is None or not self._config.firefox.enabled:
            raise RuntimeError(
                "This agent did not declare an enabled [firefox] in "
                "hackbot.toml; no Firefox build is available."
            )
        from agent_tools.firefox import FirefoxContext

        return FirefoxContext.from_source_repo(
            self.source_repo, objdir=self._config.firefox.objdir
        )

    @cached_property
    def anthropic(self) -> AnthropicAuth:
        """Anthropic credentials (validated on first key access)."""
        return AnthropicAuth()

    # --- Results / artifacts / actions plumbing ----------------------- #

    @cached_property
    def uploader(self) -> SignedPolicyUploader | None:
        if not self.results_policy_url:
            return None
        return SignedPolicyUploader(
            url=self.results_policy_url,
            fields=self.results_policy_fields,
            prefix=self.results_prefix,
        )

    @cached_property
    def run_artifacts_dir(self) -> Path:
        """Per-run local artifacts directory: ``artifacts_dir / run_id``."""
        return self.artifacts_dir / self.run_id

    @cached_property
    def log_path(self) -> Path:
        """A writable path for the agent's run log; published by the runtime.

        The parent dir is created on first access (so a ``Reporter`` can open the
        file straight away). Agents that never write a log just leave it absent,
        and :meth:`publish_log` becomes a no-op.
        """
        return Path(tempfile.mkdtemp(prefix=f"hackbot-{self.run_id}-")) / "agent.log"

    @cached_property
    def actions(self) -> ActionsRecorder:
        return ActionsRecorder(self.uploader, artifacts_dir=self.run_artifacts_dir)

    def publish_file(
        self, key: str, path: Path, content_type: str | None = None
    ) -> str:
        """Upload ``path`` under ``key``, else copy it into the artifacts dir."""
        return artifacts.publish_file(
            self.uploader, self.run_artifacts_dir, key, path, content_type
        )

    def publish_json(self, key: str, payload: dict) -> str:
        """Upload ``payload`` JSON under ``key``, else write it to artifacts."""
        return artifacts.publish_json(
            self.uploader, self.run_artifacts_dir, key, payload
        )

    def publish_changes(
        self,
        patch_key: str = "changes/changes.patch",
        meta_key: str = "changes/changes.json",
        phabricator_diff_key: str = "changes/phabricator_diff.json",
    ) -> str | None:
        """Collect the agent's source-tree changes and publish them as artifacts.

        Produces an mbox patch (applied with ``git am``) that preserves any
        local commits and wraps the uncommitted remainder, plus a JSON summary.
        Returns the patch key, or ``None`` when the agent never prepared a source
        checkout or made no changes at all.

        If the agent recorded a ``phabricator.submit_patch`` action, also builds
        and publishes the Phabricator submission payload here — while the
        checkout the agent already has is still around — so the downstream
        apply step never needs its own checkout (see
        ``changes.build_phabricator_diff``).
        """
        if self._source_base is None:
            return None
        change_set = changes.collect(
            self.source_repo, self._source_base, self._config.source.repo_url
        )
        if change_set is None:
            return None
        artifacts.publish_bytes(
            self.uploader,
            self.run_artifacts_dir,
            patch_key,
            change_set.patch,
            "text/x-patch",
        )
        self.publish_json(meta_key, change_set.metadata)

        wants_phabricator = any(
            action["type"] == "phabricator.submit_patch"
            for action in self.actions.actions
        )
        if wants_phabricator:
            diff_payload = changes.build_phabricator_diff(
                self.source_repo, self._source_base, self._config.source.repo_url
            )
            if diff_payload is not None:
                self.publish_json(phabricator_diff_key, diff_payload)

        return patch_key
