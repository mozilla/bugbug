FROM python:3.14 AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

# Install external deps without building workspace members.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=VERSION,target=VERSION \
    uv sync --frozen --no-dev --no-install-workspace --package hackbot-agent-gecko-triage

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,target=/app,rw \
    uv sync --locked --no-dev --no-editable --package hackbot-agent-gecko-triage

FROM python:3.14 AS base

COPY --from=builder /opt/venv /opt/venv
WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PATH="/opt/venv/bin:$PATH"

FROM base AS agent

# Node and profiler-cli, so the agent can read the Firefox Profiler captures
# reporters attach. profiler-cli 0.11.0 requires Node >= 24.
# Node comes from the official image rather than a distro package because
# bookworm ships Node 18.
COPY --from=node:24-bookworm-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=node:24-bookworm-slim /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && npm install -g @firefox-devtools/profiler-cli@0.11.0 \
    && npm cache clean --force

# A static webspec-index, so spec-check can read web specs by section. Release
# 0.14.0 has a static musl build only for x86_64, so arm64 fails the build.
ARG TARGETARCH
ARG WEBSPEC_INDEX_VERSION=0.14.0
ARG WEBSPEC_INDEX_SHA256_AMD64=24a20b9d2f24151b0182001accfdf1bca09566f5822b85fc2321750e37cf0993
RUN case "${TARGETARCH:-amd64}" in \
        amd64) arch=x86_64 sha="${WEBSPEC_INDEX_SHA256_AMD64}" ;; \
        *) echo "webspec-index ${WEBSPEC_INDEX_VERSION} has no static build for ${TARGETARCH}" >&2; exit 1 ;; \
    esac \
    && curl -fsSL -o /tmp/webspec-index.tgz \
        "https://github.com/jnjaeschke/webspec-index/releases/download/v${WEBSPEC_INDEX_VERSION}/webspec-index-${arch}-unknown-linux-musl.tar.gz" \
    && echo "${sha}  /tmp/webspec-index.tgz" | sha256sum -c - \
    && tar -xzf /tmp/webspec-index.tgz -C /usr/local/bin webspec-index \
    && rm /tmp/webspec-index.tgz

# ffmpeg and ffprobe, so fetch_attachment can read video attachments and screen
# recordings. Not pinned: the Debian archive keeps only the current security release,
# so a pinned version breaks the build after the next ffmpeg update.
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# fx-bug-toolkit, whose analyze-profile, check-log, spec-check and bug-start skills the
# subagents follow, pinned to the commit of tag fx-bug-toolkit--v0.7.0 so a moved tag
# cannot change the image. Only the manifest and the skills are kept. The last step fails the build if
# the plugin version is not TOOLKIT_VERSION in toolkit.py.
ARG FX_BUG_TOOLKIT_COMMIT=cf6aa429ae937ece55da3cf350177742182d6f94
RUN git init -q /tmp/fx-bug-toolkit \
    && git -C /tmp/fx-bug-toolkit fetch -q --depth 1 \
        https://github.com/alastor0325/fx-bug-toolkit "$FX_BUG_TOOLKIT_COMMIT" \
    && git -C /tmp/fx-bug-toolkit checkout -q FETCH_HEAD \
    && mkdir /opt/fx-bug-toolkit \
    && mv /tmp/fx-bug-toolkit/.claude-plugin /tmp/fx-bug-toolkit/skills /opt/fx-bug-toolkit/ \
    && rm -rf /tmp/fx-bug-toolkit \
    && python -m hackbot_agents.gecko_triage.toolkit /opt/fx-bug-toolkit

# hackbot.toml lives at the agent root (not inside the package), so copy it into
# the working dir; the runtime discovers it there (cwd) at startup.
COPY agents/gecko-triage/hackbot.toml /app/hackbot.toml

RUN useradd --create-home --shell /bin/bash agent \
    && mkdir -p /workspace \
    && chown agent:agent /workspace

USER agent

CMD ["python", "-m", "hackbot_agents.gecko_triage"]

FROM base AS broker

RUN useradd --create-home --shell /bin/bash broker

USER broker

EXPOSE 8765

CMD ["python", "-m", "hackbot_agents.gecko_triage.broker"]
