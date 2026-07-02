from __future__ import annotations

import logging
from typing import Mapping

import requests
from pydantic.types import Json

log = logging.getLogger("hackbot_runtime.remote_config")


def load_remote_config(config_url: str | None) -> Mapping[str, Json] | None:
    if config_url is None:
        return None

    response = requests.get(config_url, timeout=30)
    response.raise_for_status()
    config = response.json()

    if not isinstance(config, dict):
        raise ValueError(
            f"Config fetched from {config_url} was not a JSON object; got {type(config).__name__}"
        )

    return config
