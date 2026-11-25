import logging
from collections.abc import Callable

import uvicorn
from pydantic_settings import BaseSettings
from starlette.types import ASGIApp


class _ServerSettings(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 8765


def run_broker(build_app: Callable[[], ASGIApp]) -> None:
    """Serve a broker's app with the setup every broker shares."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    app = build_app()
    server = _ServerSettings()
    uvicorn.run(app, host=server.host, port=server.port, log_config=None)
