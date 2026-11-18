FROM python:3.14 AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,target=/app,rw \
    uv sync --locked --no-dev --no-editable --package hackbot-agent-perf-regression

FROM node:24-bookworm-slim AS profiler
WORKDIR /opt/profiler
COPY agents/perf-regression/package.json agents/perf-regression/package-lock.json ./
RUN npm ci --omit=dev

# mozcheck has no prebuilt Linux ARM64 artifact on some historical revisions.
# Let mach build that small linter from the checkout when bootstrap cannot fetch it.
FROM rust:1.90-slim-bookworm AS rust

FROM python:3.14 AS base
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
WORKDIR /app

FROM base AS agent
COPY --from=rust /usr/local/cargo /usr/local/cargo
COPY --from=rust /usr/local/rustup /usr/local/rustup
ENV RUSTUP_HOME=/usr/local/rustup
ENV PATH="/home/agent/.cargo/bin:/usr/local/cargo/bin:$PATH"
COPY --from=profiler /usr/local/bin/node /usr/local/bin/node
COPY --from=profiler /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
COPY --from=profiler /opt/profiler /opt/profiler
RUN ln -s /opt/profiler/node_modules/.bin/profiler-cli /usr/local/bin/profiler-cli \
    && ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && useradd --create-home --shell /bin/bash agent \
    && mkdir -p /workspace \
    && chown agent:agent /workspace
COPY agents/perf-regression/hackbot.toml /app/hackbot.toml
USER agent
CMD ["python", "-m", "hackbot_agents.perf_regression"]
