import os
from subprocess import check_output

import sentry_sdk

__version__ = "0.1.0"


sentry_sdk.init(
    dsn="https://3726cbceae95af89ccedaebc8c708b4a@o1069899.ingest.us.sentry.io/4510325184462848",
    environment=os.getenv("ENVIRONMENT", "development"),
    release=f"bugbug-mcp@{__version__}",
    dist=os.getenv("IMAGE_ID"),
    server_name=check_output("hostname").decode("utf-8").rstrip(),
    # Add data like request headers and IP for users,
    # see https://docs.sentry.io/platforms/python/data-management/data-collected/ for more info
    send_default_pii=True,
)
