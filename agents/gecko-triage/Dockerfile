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
# reporters attach. profiler-cli 0.12.0 requires Node >= 24.
# Node comes from the official image rather than a distro package because
# bookworm ships Node 18.
COPY --from=node:24-bookworm-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=node:24-bookworm-slim /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && npm install -g @firefox-devtools/profiler-cli@0.12.0 \
    && npm cache clean --force

# Command-line tools from their own release builds, installed by a pinned and checked
# cargo-binstall, each at an exact version. It never falls back to cargo-quickinstall's
# builds or to a build from source, and it sends no telemetry.
# - webspec-index, so spec-check can read web specs by section.
# - socorro-cli, so a run can read a crash report from crash-stats.
ARG TARGETARCH
ARG CARGO_BINSTALL_VERSION=1.26.2
ARG CARGO_BINSTALL_SHA256_AMD64=9e0e064f189a9abb9f8df5fb93c3c810ef5cc19e7212d998c7a64c9e4d3527d5
ARG CARGO_BINSTALL_SHA256_ARM64=a235250cd684af9bf1e3a1e4c216b1d36e6c553553d5d789744f27689d81c2d6
RUN case "${TARGETARCH:-amd64}" in \
        amd64) arch=x86_64 sha="${CARGO_BINSTALL_SHA256_AMD64}" ;; \
        arm64) arch=aarch64 sha="${CARGO_BINSTALL_SHA256_ARM64}" ;; \
        *) echo "cargo-binstall has no build for ${TARGETARCH}" >&2; exit 1 ;; \
    esac \
    && curl -fsSL -o /tmp/cargo-binstall.tgz \
        "https://github.com/cargo-bins/cargo-binstall/releases/download/v${CARGO_BINSTALL_VERSION}/cargo-binstall-${arch}-unknown-linux-musl.tgz" \
    && echo "${sha}  /tmp/cargo-binstall.tgz" | sha256sum -c - \
    && tar -xzf /tmp/cargo-binstall.tgz -C /tmp cargo-binstall \
    && /tmp/cargo-binstall --no-confirm --no-track --strategies crate-meta-data \
        --disable-telemetry \
        --install-path /usr/local/bin webspec-index@0.14.0 socorro-cli@0.6.0 \
    && rm /tmp/cargo-binstall /tmp/cargo-binstall.tgz
# These tools would check crates.io for a newer version on each run.
ENV MOZTOOLS_UPDATE_CHECK=0

# ffmpeg and ffprobe, so fetch_attachment can read video attachments and screen
# recordings, and libdbus-1-3, which socorro-cli links against. Not pinned: the Debian
# archive keeps only the current security release, so a pinned version breaks the build
# after the next ffmpeg update.
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ffmpeg libdbus-1-3 \
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
