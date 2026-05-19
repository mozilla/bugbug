FROM python:3.12 AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Workspace metadata.
COPY pyproject.toml uv.lock VERSION ./

# pyproject.toml of every workspace member uv needs to keep its
# lockfile coherent.
COPY http_service/pyproject.toml ./http_service/pyproject.toml
COPY services/hackbot-api/pyproject.toml ./services/hackbot-api/pyproject.toml

# Workspace members the agent image actually needs (source included).
COPY agents/bug-fix ./agents/bug-fix
COPY bugbug ./bugbug
COPY libs/hackbot-runtime ./libs/hackbot-runtime

RUN uv sync --locked --no-dev --package hackbot-agent-bug-fix

FROM python:3.12 AS base

COPY --from=builder /app /app
WORKDIR /app/agents/bug-fix

ENV PYTHONUNBUFFERED=1
ENV PATH="/app/.venv/bin:$PATH"

FROM base AS agent

RUN useradd --create-home --shell /bin/bash agent \
    && mkdir -p /workspace \
    && chown agent:agent /workspace

ENV PYTHONDONTWRITEBYTECODE=1
USER agent

CMD ["python", "-m", "agent_runner"]

FROM base AS broker

EXPOSE 8765

CMD ["python", "-m", "broker"]
