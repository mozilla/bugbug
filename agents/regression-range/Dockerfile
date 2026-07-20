FROM python:3.12 AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

# Install external deps without building workspace members.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=VERSION,target=VERSION \
    uv sync --frozen --no-dev --no-install-workspace --package hackbot-agent-regression-range

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,target=/app,rw \
    uv sync --locked --no-dev --no-editable --package hackbot-agent-regression-range

# mozregression is installed from the fork carrying PR #2197 (the --prompt mode
# is not yet in a release), so it lives outside the workspace lockfile. Pin to a
# specific commit once the PR settles; the branch tracks the PR for now.
ARG MOZREGRESSION_REF="git+https://github.com/msujaws/mozregression.git@prompt-devtools-mcp-verdict"
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /opt/venv "mozregression @ ${MOZREGRESSION_REF}"

FROM python:3.12 AS base

COPY --from=builder /opt/venv /opt/venv
WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PATH="/opt/venv/bin:$PATH"

FROM base AS agent

# mozregression's --prompt mode (mozilla/mozregression#2197) shells out to the
# Claude CLI, which launches the Firefox DevTools MCP via npx to classify each
# build. So the agent image needs Node.js/npm/npx, the DevTools MCP, the Claude
# CLI, and the shared libraries a downloaded Firefox needs to run headless. The
# Firefox builds themselves are fetched by mozregression at bisect time.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        # Node.js/npm/npx: run the Claude CLI and the Firefox DevTools MCP.
        nodejs npm \
        ca-certificates \
        # Firefox runtime deps (for the builds mozregression downloads).
        libgtk-3-0 libdbus-glib-1-2 libx11-xcb1 libxtst6 libxt6 libpci3 \
        libnss3 libnspr4 libasound2t64 libpango-1.0-0 libcairo2 \
        fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# The Claude CLI (invoked by mozregression --prompt) and the Firefox DevTools
# MCP it drives. mozregression launches the MCP with `npx --prefer-offline
# @mozilla/firefox-devtools-mcp`, so a global install keeps the run offline and
# fast (npx resolves the globally-installed package before hitting the network).
RUN npm install -g @anthropic-ai/claude-code @mozilla/firefox-devtools-mcp

# hackbot.toml lives at the agent root (not inside the package), so copy it into
# the working dir; the runtime discovers it there (cwd) at startup.
COPY agents/regression-range/hackbot.toml /app/hackbot.toml

RUN useradd --create-home --shell /bin/bash agent \
    && mkdir -p /workspace \
    && chown agent:agent /workspace

USER agent

CMD ["python", "-m", "hackbot_agents.regression_range"]

FROM base AS broker

RUN useradd --create-home --shell /bin/bash broker

USER broker

EXPOSE 8765

CMD ["python", "-m", "hackbot_agents.regression_range.broker"]
