FROM python:3.14 AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_PROJECT_ENVIRONMENT=/opt/venv
ENV UV_PYTHON_DOWNLOADS=never

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

# treeherder-cli (MPL-2.0) lets the agent query Firefox CI: what failed on a push,
# when a failure started, how often a job passes. Built from source because upstream
# only ships prebuilt Linux binaries for x86_64, and `cargo install` also works for an
# arm64 developer build. A bookworm builder is safe against either python:3.14 base
# (same glibc, or older building for newer); rustls means no OpenSSL to link.
# Built from the evgenyrp fork until the User-Agent fix (Treeherder rejects requests
# without one) is released upstream: https://github.com/padenot/treeherder-cli/pull/12
FROM rust:1-slim-bookworm AS treeherder-cli
ARG TREEHERDER_CLI_GIT=https://github.com/evgenyrp/treeherder-cli.git
ARG TREEHERDER_CLI_REV=0095956a9db27306e2b5dc3b524870e893fd4a86
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/usr/local/cargo/git \
    --mount=type=cache,target=/build/target \
    CARGO_TARGET_DIR=/build/target/${TREEHERDER_CLI_REV} \
    cargo install --git ${TREEHERDER_CLI_GIT} --rev ${TREEHERDER_CLI_REV} --locked --root /out treeherder-cli

FROM python:3.14 AS base

COPY --from=builder /opt/venv /opt/venv
WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PATH="/opt/venv/bin:$PATH"

FROM base AS agent

# hackbot.toml lives at the agent root (not inside the package), so copy it into
# the working dir; the runtime discovers it there (cwd) at startup.
COPY agents/test-repair/hackbot.toml /app/hackbot.toml

RUN useradd --create-home --shell /bin/bash agent \
    && mkdir -p /workspace \
    && chown agent:agent /workspace

# /usr/local/bin is on PATH for every user and nothing at runtime writes to it.
COPY --from=treeherder-cli /out/bin/treeherder-cli /usr/local/bin/treeherder-cli

USER agent

CMD ["python", "-m", "hackbot_agents.test_repair"]
