# Build stage
FROM python:3.14-slim AS builder

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

# Install external deps without building workspace members.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=VERSION,target=VERSION \
    uv sync --frozen --no-dev --no-install-workspace --package reviewhelper-api

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,target=/app,rw \
    uv sync --locked --no-dev --no-editable --package reviewhelper-api

# Runtime stage
FROM python:3.14-slim AS base

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

# Enable unbuffered mode will ensure real-time logging
ENV PYTHONUNBUFFERED=1
ENV PORT=8080
ENV PATH="/opt/venv/bin:$PATH"

EXPOSE 8080

# Run the server
CMD uvicorn app.main:app --host 0.0.0.0 --port $PORT
