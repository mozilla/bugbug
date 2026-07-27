FROM python:3.12 AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

# Install external deps without building workspace members.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=VERSION,target=VERSION \
    uv sync --frozen --no-dev --no-install-workspace --package hackbot-agent-test-repair

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,target=/app,rw \
    uv sync --locked --no-dev --no-editable --package hackbot-agent-test-repair

FROM python:3.12 AS base

COPY --from=builder /opt/venv /opt/venv
WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PATH="/opt/venv/bin:$PATH"

FROM base AS agent

# Xvfb plus Firefox's runtime libraries and fonts, so the agent can actually run
# the failing test locally. `mach bootstrap` installs the *build* toolchain but
# runs unprivileged here, so it cannot install these system packages itself.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        xvfb x11-utils \
        fonts-liberation fonts-dejavu-core \
        libgtk-3-0t64 libdbus-glib-1-2 libx11-xcb1 libxtst6 libxt6 libpci3 \
        libasound2t64 libnss3 libnspr4 libgbm1 libdrm2 libxcomposite1 \
        libxdamage1 libxfixes3 libxrandr2 libxkbcommon0 libpango-1.0-0 \
        libcairo2 libatk1.0-0t64 libatk-bridge2.0-0t64 libcups2t64 \
        libatspi2.0-0t64 \
        zip unzip bzip2 \
    && rm -rf /var/lib/apt/lists/*

# hackbot.toml lives at the agent root (not inside the package), so copy it into
# the working dir; the runtime discovers it there (cwd) at startup.
COPY agents/test-repair/hackbot.toml /app/hackbot.toml
COPY agents/test-repair/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

RUN useradd --create-home --shell /bin/bash agent \
    && mkdir -p /workspace \
    && chown agent:agent /workspace \
    # Xvfb runs unprivileged and cannot create its own socket directory.
    && mkdir -p /tmp/.X11-unix \
    && chmod 1777 /tmp/.X11-unix

# `mach bootstrap` installs the toolchain here at runtime; put it on PATH so the
# agent's own `./mach build` (and the build_firefox tool) find rustc/clang.
ENV PATH="/home/agent/.cargo/bin:/home/agent/.mozbuild/clang/bin:${PATH}"
ENV DISPLAY=:99

USER agent

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["python", "-m", "hackbot_agents.test_repair"]
