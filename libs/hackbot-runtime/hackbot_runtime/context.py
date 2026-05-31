import datetime
import uuid
from functools import cached_property
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from hackbot_runtime import artifacts
from hackbot_runtime.actions import ActionsRecorder
from hackbot_runtime.uploader import SignedPolicyUploader


def _default_run_id() -> str:
    """A unique, sortable id for runs that don't get one from the platform.

    The orchestrator sets ``RUN_ID`` explicitly in production; this fallback
    keeps local/compose/direct runs unique (so per-run artifact dirs don't
    collide). The ``local-`` prefix marks it as not platform-assigned.
    """
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"local-{stamp}-{uuid.uuid4().hex[:6]}"


class Context(BaseSettings):
    """Platform context handed to every agent's main() by the runtime.

    `run_id` defaults to a generated unique id (the orchestrator overrides it
    via ``RUN_ID`` in production). The results-upload fields are optional so
    local-dev runs (compose, scripts) can start the agent without a
    signed POST policy — in that case the runtime writes results into the
    local artifacts dir rather than uploading.
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
