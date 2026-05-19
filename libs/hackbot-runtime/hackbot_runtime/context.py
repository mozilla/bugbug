from functools import cached_property

from pydantic_settings import BaseSettings, SettingsConfigDict

from hackbot_runtime.uploader import SignedPolicyUploader


class Context(BaseSettings):
    """Platform context handed to every agent's main() by the runtime.

    `run_id` is required. The results-upload fields are optional so
    local-dev runs (compose, scripts) can start the agent without a
    signed POST policy — in that case the runtime skips the summary
    upload and logs a warning rather than failing.
    """

    run_id: str
    results_prefix: str = ""
    results_policy_url: str | None = None
    results_policy_fields: dict[str, str] = {}

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
