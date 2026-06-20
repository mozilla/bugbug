# Build stage
FROM python:3.14-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

# Install external deps without building workspace members.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=VERSION,target=VERSION \
    uv sync --frozen --no-dev --no-install-workspace --package bugbug-mcp

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,target=/app,rw \
    uv sync --locked --no-dev --no-editable --package bugbug-mcp

# Resource generation stage
FROM builder AS resource-generator

WORKDIR /app

# The llms-txt generator needs the optional `llms-txt` dependencies, which are
# not part of the runtime image; install them only in this throwaway stage.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=VERSION,target=VERSION \
    uv sync --frozen --no-dev --no-install-workspace --extra llms-txt --package bugbug-mcp

RUN --mount=type=bind,source=services/mcp/scripts/llms-txt.py,target=scripts/llms-txt.py \
    /opt/venv/bin/python scripts/llms-txt.py

# Runtime stage
FROM python:3.14-slim AS base

COPY --from=builder /opt/venv /opt/venv
COPY --from=resource-generator /app/static/llms.txt /app/static/llms.txt

WORKDIR /app

ENV PATH="/opt/venv/bin:$PATH"

# Enable unbuffered mode will ensure real-time logging
ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "bugbug_mcp.server"]
