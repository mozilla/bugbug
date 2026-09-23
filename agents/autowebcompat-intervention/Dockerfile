FROM python:3.12 AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

# Install external deps without building workspace members.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=VERSION,target=VERSION \
    uv sync --frozen --no-dev --no-install-workspace --package hackbot-agent-autowebcompat-intervention

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,target=/app,rw \
    uv sync --locked --no-dev --no-editable --package hackbot-agent-autowebcompat-intervention

FROM python:3.12 AS base

COPY --from=builder /opt/venv /opt/venv
WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PATH="/opt/venv/bin:$PATH"

FROM base AS agent

# This agent both builds Firefox and drives it, so it needs the union of the
# build toolchain and the browser's runtime libraries.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        # ./mach needs mercurial for its vcs checks even on a git checkout.
        mercurial git curl zip unzip \
        # Node.js, to run the Firefox DevTools MCP server.
        nodejs npm \
        ca-certificates \
        # Firefox runtime deps (the build we produce has to actually launch).
        libgtk-3-0 libdbus-glib-1-2 libx11-xcb1 libxtst6 libxt6 libpci3 \
        libasound2t64 libpango-1.0-0 libcairo2 libxcomposite1 libxdamage1 \
        libxfixes3 libxrandr2 libxkbcommon0 libnss3 libnspr4 \
        fonts-liberation \
        # Codecs, for sites whose breakage involves media.
        ffmpeg \
        # Headless display.
        fluxbox xvfb \
    && rm -rf /var/lib/apt/lists/*

# Install the DevTools MCP server from the pinned package.json/lockfile.
COPY agents/autowebcompat-intervention/package.json \
     agents/autowebcompat-intervention/package-lock.json \
     /app/autowebcompat-intervention/
RUN cd /app/autowebcompat-intervention && npm ci --omit=dev

# hackbot.toml lives at the agent root (not inside the package), so copy it into
# the working dir; the runtime discovers it there (cwd) at startup.
COPY agents/autowebcompat-intervention/hackbot.toml /app/hackbot.toml

RUN useradd --create-home --shell /bin/bash agent \
    && mkdir -p /workspace \
    && chown agent:agent /workspace /app/autowebcompat-intervention

ENV SCREEN_WIDTH=1280
ENV SCREEN_HEIGHT=1024
ENV SCREEN_DEPTH=24
ENV DISPLAY=:99.0

# ./mach writes its state (including the artifact-build cache) under here, so
# it must belong to the agent user for a warm rebuild to work.
ENV MOZBUILD_STATE_PATH=/home/agent/.mozbuild

USER agent

CMD ["python", "-m", "hackbot_agents.autowebcompat_intervention"]

FROM base AS broker

RUN useradd --create-home --shell /bin/bash broker

USER broker

EXPOSE 8765

CMD ["python", "-m", "hackbot_agents.autowebcompat_intervention.broker"]
