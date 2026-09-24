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

# The `bisector` subagent runs mozregression's --prompt mode, which shells out to
# the Claude CLI, which launches the Firefox DevTools MCP via npx to judge each
# build. So the agent image needs Node.js, both of those, and the shared libraries
# a downloaded Firefox needs to run headless. mozregression fetches the builds.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        nodejs npm \
        ca-certificates \
        libgtk-3-0 libdbus-glib-1-2 libx11-xcb1 libxtst6 libxt6 libpci3 \
        libnss3 libnspr4 libasound2t64 libpango-1.0-0 libcairo2 \
        fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# mozregression launches the MCP with `npx --prefer-offline`, which resolves a
# global install before touching the network.
RUN npm install -g @anthropic-ai/claude-code @mozilla/firefox-devtools-mcp

# Cut every Firefox the bisection runs off from the network. Bug text decides what
# the builds open, so an open build would let a bug aim us at any site. A policy
# rather than prefs, because the DevTools MCP launches each build with a fresh
# profile that mozregression's --pref never reaches; Firefox reads this file for
# every build and profile, and "Locked" keeps the judge from changing it through
# the MCP. about:, chrome: and data: pages still load.
COPY agents/frontend-triage/firefox-policies.json /etc/firefox/policies/policies.json

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
