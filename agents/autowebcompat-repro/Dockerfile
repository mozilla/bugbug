FROM python:3.12 AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

# Install external deps without building workspace members.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=VERSION,target=VERSION \
    uv sync --frozen --no-dev --no-install-workspace --package hackbot-agent-autowebcompat-repro

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,target=/app,rw \
    uv sync --locked --no-dev --no-editable --package hackbot-agent-autowebcompat-repro

FROM python:3.12 AS base

COPY --from=builder /opt/venv /opt/venv
WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PATH="/opt/venv/bin:$PATH"

FROM base AS agent

# The Firefox DevTools MCP server is an npm package launched via `npx`, so the
# agent image needs Node.js + npm (the python base ships neither). It also
# needs the shared libraries Firefox requires to run headless; the Firefox
# binary itself is downloaded at agent startup (a fresh Nightly per run) via
# mozdownload/mozinstall, not baked in here.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        nodejs npm \
        ca-certificates \
        libgtk-3-0 libdbus-glib-1-2 libx11-xcb1 libxtst6 libxt6 \
        libasound2 libpci3 \
    && rm -rf /var/lib/apt/lists/*

# hackbot.toml lives at the agent root (not inside the package), so copy it into
# the working dir; the runtime discovers it there (cwd) at startup.
COPY agents/autowebcompat-repro/hackbot.toml /app/hackbot.toml

RUN useradd --create-home --shell /bin/bash agent \
    && mkdir -p /workspace \
    && chown agent:agent /workspace

USER agent

CMD ["python", "-m", "hackbot_agents.autowebcompat_repro"]

FROM base AS broker

RUN useradd --create-home --shell /bin/bash broker

USER broker

EXPOSE 8765

CMD ["python", "-m", "hackbot_agents.autowebcompat_repro.broker"]