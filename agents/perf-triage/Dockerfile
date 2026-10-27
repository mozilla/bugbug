FROM python:3.14 AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=VERSION,target=VERSION \
    uv sync --frozen --no-dev --no-install-workspace --package hackbot-agent-perf-triage

# The agent's own source enters the image through this bind mount rather than a
# COPY, so its contents are NOT part of this layer's cache key: editing agent.py
# or anything under libs/performance-tools does not invalidate the layer, and a plain
# rebuild silently ships the previously-built code. That is harmless in CI
# (every build starts cold) but wrong for local iteration — it cost three real
# agent runs against a stale image before it was spotted in perf-repro.
#
# SOURCE_REF busts the cache without needing --no-cache (which would also
# discard the apt layers). compose.yml passes the current git HEAD, so any
# commit rebuilds the source layer and nothing else.
ARG SOURCE_REF=unknown
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,target=/app,rw \
    echo "source ref: ${SOURCE_REF}" \
    && uv sync --locked --no-dev --no-editable --package hackbot-agent-perf-triage

FROM python:3.14 AS base

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PATH="/opt/venv/bin:$PATH"

FROM base AS agent

# profiler-cli for the triage skill's profile step: the profiler-analysis skill
# self-checks for it on PATH and hard-stops without it. No browser libraries --
# this agent never launches Firefox. jq: agent-mode Step 5 checks its JSON with it
# before emitting the result.
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm ca-certificates jq \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g @firefox-devtools/profiler-cli \
    && npm cache clean --force

# searchfox-cli, the second tool the skill reaches for. Unlike profiler-cli it
# does NOT hard-stop the skill when absent — it is one of three named routes for
# validating an inference (marker payloads and sample counts are the others), so
# the skill degrades rather than refusing. It is still worth having: --calls-from
# /--calls-to/--can-gc/--field-layout are exactly what root-cause work uses, and
# hackbot_runtime/searchfox.py exposes none of them.
#
# The plan assumed `cargo install`, which would mean a full Rust toolchain
# (rustc+cargo+libllvm is ~1.2GB unpacked) plus a from-source build on every
# cold image. Upstream publishes prebuilt static-ish Linux binaries per release,
# so this is a ~5MB tarball instead. Pinned to a version, not "latest", for the
# same reason the skill itself is pinned: upgrades arrive in a reviewable diff.
ARG SEARCHFOX_CLI_VERSION=0.20.4
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      amd64) target=x86_64-unknown-linux-gnu ;; \
      arm64) target=aarch64-unknown-linux-gnu ;; \
      *) echo "no searchfox-cli build for $arch" >&2; exit 1 ;; \
    esac; \
    url="https://github.com/padenot/searchfox-cli/releases/download/v${SEARCHFOX_CLI_VERSION}/searchfox-cli-${SEARCHFOX_CLI_VERSION}-${target}.tar.gz"; \
    curl -fsSL "$url" -o /tmp/searchfox-cli.tar.gz; \
    tar -xzf /tmp/searchfox-cli.tar.gz -C /tmp; \
    install -m 0755 "$(find /tmp -type f -name searchfox-cli | head -1)" /usr/local/bin/searchfox-cli; \
    rm -rf /tmp/searchfox-cli.tar.gz /tmp/searchfox-cli-*; \
    searchfox-cli --version

# The profiler-analysis skill, vendored at a pinned mozilla-central revision in
# libs/performance-tools/performance_tools/skills/ (see PINNED.md there). The SDK discovers
# skills on the filesystem at <cwd>/.claude/skills/ and WORKDIR is /app, so this
# exact path is what makes ClaudeAgentOptions.skills=["profiler-analysis"] find
# it — proven in-container 2026-08-19.
#
# references/ is load-bearing, not documentation: the skill Globs that directory
# for the case study matching the scenario. Copying SKILL.md alone leaves the
# skill RUNNING but uncalibrated, which fails silently. Copy the whole tree.
COPY libs/performance-tools/performance_tools/skills/profiler-analysis \
     /app/.claude/skills/profiler-analysis

# The triage-perf-bug skill (step 1), vendored at a pinned revision of
# performance-triage-calculator -- see PINNED.md. Same /app/.claude/skills/
# discovery path as profiler-analysis above. One file, no references/ tree.
COPY libs/performance-tools/performance_tools/skills/triage-perf-bug \
     /app/.claude/skills/triage-perf-bug

# The calculator the skill shells out to. Vendored at the same revision, and
# REQUIRED: the skill resolves $PERF_TRIAGE_PATH first and otherwise falls back
# to its own repository root, which does not exist in this image -- so an unset
# PERF_TRIAGE_PATH means step 1 cannot run. Setting it here rather than in
# compose.yml makes the image self-sufficient; compose still passes it through
# so a local run can point at a working checkout instead.
COPY libs/performance-tools/performance_tools/calculator/perf_triage.py /app/perf_triage.py
# --classify-domain reads the pages-affected tiers from beside the script.
COPY libs/performance-tools/performance_tools/calculator/top_sites.json /app/top_sites.json
ENV PERF_TRIAGE_PATH=/app/perf_triage.py

# hackbot.toml lives at the agent root; the runtime discovers it in the cwd.
COPY agents/perf-triage/hackbot.toml /app/hackbot.toml

RUN useradd --create-home --shell /bin/bash agent \
    && mkdir -p /workspace \
    && chown agent:agent /workspace \
    && chown -R agent:agent /app/.claude

USER agent

COPY --from=builder /opt/venv /opt/venv

CMD ["python", "-m", "hackbot_agents.perf_triage"]

FROM base AS broker

RUN useradd --create-home --shell /bin/bash broker

USER broker

EXPOSE 8765

COPY --from=builder /opt/venv /opt/venv

CMD ["python", "-m", "performance_tools.broker"]
