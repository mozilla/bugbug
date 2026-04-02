ARG BUGBUG_VERSION=latest

FROM mozilla/bugbug-base:$BUGBUG_VERSION

# Install dependencies first
RUN --mount=type=bind,source=pyproject.toml,target=/tmp/workspace/pyproject.toml \
	--mount=type=bind,source=uv.lock,target=/tmp/workspace/uv.lock \
	--mount=type=bind,source=VERSION,target=/tmp/workspace/VERSION \
	--mount=type=bind,source=http_service/pyproject.toml,target=/tmp/workspace/http_service/pyproject.toml \
	cd /tmp/workspace && uv sync --frozen --no-dev --package bugbug-http-service --no-install-project

# Setup http service as package
RUN --mount=type=bind,target=/tmp/workspace,rw \
	cd /tmp/workspace && uv sync --frozen --no-dev --package bugbug-http-service --no-editable

# Run the Pulse listener in the background
CMD (bugbug-http-pulse-listener &) && gunicorn -b 0.0.0.0:$PORT bugbug_http.app --preload --timeout 30 -w 3
