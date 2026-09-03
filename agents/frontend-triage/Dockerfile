FROM python:3.12 AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

# Install external deps without building workspace members.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=VERSION,target=VERSION \
    uv sync --frozen --no-dev --no-install-workspace --package hackbot-agent-frontend-triage

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,target=/app,rw \
    uv sync --locked --no-dev --no-editable --package hackbot-agent-frontend-triage

FROM python:3.12 AS base

COPY --from=builder /opt/venv /opt/venv
WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PATH="/opt/venv/bin:$PATH"

FROM base AS agent

# hackbot.toml lives at the agent root (not inside the package), so copy it into
# the working dir; the runtime discovers it there (cwd) at startup.
COPY agents/frontend-triage/hackbot.toml /app/hackbot.toml

RUN useradd --create-home --shell /bin/bash agent \
    && mkdir -p /workspace \
    && chown agent:agent /workspace

USER agent

CMD ["python", "-m", "hackbot_agents.frontend_triage"]

FROM base AS broker

RUN useradd --create-home --shell /bin/bash broker

USER broker

EXPOSE 8765

CMD ["python", "-m", "hackbot_agents.frontend_triage.broker"]
