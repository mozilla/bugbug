from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Deploy-time configuration. Nothing here varies per run."""

    # Upstream Bugzilla. The credential lives only here, in this one service.
    upstream_url: str = "https://bugzilla.mozilla.org/rest"
    upstream_api_key: str = ""
    upstream_timeout_seconds: float = 30.0

    # hackbot-api's service account email, and the whole trust configuration:
    # tokens are signed by that account's Google-managed key, and we verify
    # against the certs Google publishes for it, so there is no key material to
    # provision or rotate. The cert URL is derived from this, so only that
    # account's keys are ever candidates.
    #
    # The audience and cert URL are constants in bugzilla_proxy.tokens, not
    # settings, so neither can drift from what hackbot-api mints nor be pointed
    # somewhere it should not be.
    token_issuer: str = ""
    jwt_public_key_ttl_seconds: int = 60 * 60
    # A static PEM for local runs, where there is no service account. Mutually
    # exclusive with `token_issuer`.
    jwt_public_key: str = ""

    # Per-(token, bug) authorization decisions. Short enough that a bug moving
    # into a security group stops being served promptly, long enough that a
    # run walking a dependency tree does not re-fetch every bug per tool call.
    decision_cache_ttl_seconds: int = 300
    decision_cache_max_entries: int = 10_000

    # A ceiling on what one search can pull from upstream. Results are filtered
    # after they arrive, so an unbounded limit would mean fetching far more
    # than the caller can ever see.
    max_search_limit: int = 500

    port: int = 8080
    environment: str = "development"

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "env_prefix": "BUGZILLA_PROXY_",
        "extra": "ignore",
    }


settings = Settings()
