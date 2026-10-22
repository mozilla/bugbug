import logging
from collections.abc import Callable

import uvicorn
from pydantic_settings import BaseSettings
from starlette.types import ASGIApp


def run_broker[S: BaseSettings](
    build_app: Callable[[S], ASGIApp], settings_cls: type[S]
) -> None:
    """Serve a broker's app with the setup every broker shares."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    settings = settings_cls()
    app = build_app(settings)
    uvicorn.run(app, host=settings.host, port=settings.port, log_config=None)
