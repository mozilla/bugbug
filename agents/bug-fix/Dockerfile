FROM python:3.12 AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Workspace metadata first so the dep-download layer caches independently
# of source changes.
COPY pyproject.toml uv.lock VERSION ./
COPY http_service/pyproject.toml ./http_service/
COPY services/hackbot-api/pyproject.toml ./services/hackbot-api/
COPY agents/bug-fix/pyproject.toml ./agents/bug-fix/
COPY libs/hackbot-runtime/pyproject.toml ./libs/hackbot-runtime/

# Install external deps without building workspace members.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-workspace --package hackbot-agent-bug-fix

# Workspace members the agent image actually needs (source included).
COPY agents/bug-fix ./agents/bug-fix
COPY libs/hackbot-runtime ./libs/hackbot-runtime

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package hackbot-agent-bug-fix

FROM python:3.12 AS base

COPY --from=builder /app /app
WORKDIR /app/agents/bug-fix

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PATH="/app/.venv/bin:$PATH"

FROM base AS agent

RUN useradd --create-home --shell /bin/bash agent \
    && mkdir -p /workspace \
    && chown agent:agent /workspace

USER agent

CMD ["python", "-m", "agent_runner"]

FROM base AS broker

RUN useradd --create-home --shell /bin/bash broker

USER broker

EXPOSE 8765

CMD ["python", "-m", "broker"]
