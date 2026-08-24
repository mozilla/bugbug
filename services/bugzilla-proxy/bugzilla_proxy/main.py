"""Entry point. Fails at startup rather than per request if misconfigured."""

import logging

import uvicorn

from bugzilla_proxy.app import create_app
from bugzilla_proxy.config import settings

app = create_app(settings)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    uvicorn.run(app, host="0.0.0.0", port=settings.port, log_config=None)


if __name__ == "__main__":
    main()
