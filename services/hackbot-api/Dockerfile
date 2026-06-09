FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Workspace metadata first so the dep-download layer caches independently
# of source changes.
COPY pyproject.toml uv.lock VERSION ./
COPY http_service/pyproject.toml ./http_service/
COPY services/hackbot-api/pyproject.toml ./services/hackbot-api/
COPY agents/bug-fix/pyproject.toml ./agents/bug-fix/
COPY libs/hackbot-runtime/pyproject.toml ./libs/hackbot-runtime/
COPY libs/agent-tools/pyproject.toml ./libs/agent-tools/

# Install external deps without building workspace members.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-workspace --package hackbot-api

# Workspace member source.
COPY services/hackbot-api ./services/hackbot-api

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package hackbot-api

FROM python:3.12-slim AS base

COPY --from=builder /app /app
WORKDIR /app/services/hackbot-api

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PORT=8080
ENV PATH="/app/.venv/bin:$PATH"

RUN useradd --create-home --shell /bin/bash app
USER app

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
