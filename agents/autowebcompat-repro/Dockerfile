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

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PATH="/opt/venv/bin:$PATH"

FROM base AS agent

# The agent needs Node.js + npm to run the DevTools MCP servers (npm packages;
# the python base ships neither) and the shared libraries the browsers require
# to run headless. The browser binaries themselves are downloaded at agent
# startup (a fresh build per run): Firefox via mozdownload/mozinstall and Chrome
# for Testing via the Chrome-for-Testing JSON API (see browser.py).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        # Node.js, to run the Firefox and Chrome DevTools MCP servers.
        nodejs npm \
        # Shared by both browsers.
        ca-certificates \
        # Firefox runtime deps.
        libgtk-3-0 libdbus-glib-1-2 libx11-xcb1 libxtst6 libxt6 libpci3 \
        # Chrome runtime deps.
        libnss3 libnspr4 libatk1.0-0t64 libatk-bridge2.0-0t64 libatspi2.0-0t64 \
        libcups2t64 libdbus-1-3 libgbm1 libxcomposite1 libxdamage1 libxfixes3 \
        libxrandr2 libxkbcommon0 libasound2t64 libpango-1.0-0 libcairo2 \
        fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Install the DevTools MCP servers from the pinned package.json/lockfile
COPY agents/autowebcompat-repro/package.json \
     agents/autowebcompat-repro/package-lock.json \
     /app/node/
RUN cd /app/node && npm ci --omit=dev

# hackbot.toml lives at the agent root (not inside the package), so copy it into
# the working dir; the runtime discovers it there (cwd) at startup.
COPY agents/autowebcompat-repro/hackbot.toml /app/hackbot.toml

RUN useradd --create-home --shell /bin/bash agent \
    && mkdir -p /workspace \
    && chown agent:agent /workspace

USER agent

COPY --from=builder /opt/venv /opt/venv

CMD ["python", "-m", "hackbot_agents.autowebcompat_repro"]

FROM base AS broker

RUN useradd --create-home --shell /bin/bash broker

USER broker

EXPOSE 8765

COPY --from=builder /opt/venv /opt/venv

CMD ["python", "-m", "hackbot_agents.autowebcompat_repro.broker"]