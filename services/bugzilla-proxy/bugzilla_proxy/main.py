"""Entry point. Fails at startup rather than per request if misconfigured."""

import logging

import uvicorn

from bugzilla_proxy.app import create_app
from bugzilla_proxy.config import settings

# At import, not in `main()`: the container runs `uvicorn bugzilla_proxy.main:app`,
# which never calls `main()`, and uvicorn configures only its own loggers. Left
# to the root default of WARNING, every `audit` line would be silently dropped.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

app = create_app(settings)


def main() -> None:
    uvicorn.run(app, host="0.0.0.0", port=settings.port, log_config=None)


if __name__ == "__main__":
    main()
