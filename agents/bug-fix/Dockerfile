FROM python:3.14 AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

# Install external deps without building workspace members.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=VERSION,target=VERSION \
    uv sync --frozen --no-dev --no-install-workspace --package hackbot-agent-bug-fix

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,target=/app,rw \
    uv sync --locked --no-dev --no-editable --package hackbot-agent-bug-fix

# profiler-cli, the Firefox Profiler's command-line analyzer. It runs with the
# node `mach bootstrap` installs, so this stage only installs it.
FROM node:24-trixie-slim AS profiler-cli
ARG PROFILER_CLI_VERSION=0.10.0
RUN npm install --global --prefix /opt/profiler-cli --no-fund --no-audit \
        @firefox-devtools/profiler-cli@${PROFILER_CLI_VERSION}

FROM python:3.14 AS base

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PATH="/opt/venv/bin:$PATH"

FROM base AS agent

# The agent both builds Firefox and runs it, so it needs the build prerequisites
# plus the browser's runtime libraries. `mach bootstrap` installs the *build*
# toolchain into ~/.mozbuild, but runs unprivileged here, so it cannot install
# these system packages itself.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        # ./mach needs mercurial for its vcs checks even on a git checkout.
        mercurial git curl zip unzip bzip2 ca-certificates \
        # Debugging and inspecting: crashes, syscalls, JSON from mach and CI,
        # profile databases.
        gdb strace ripgrep jq sqlite3 \
        # Firefox runtime deps.
        libgtk-3-0t64 libdbus-glib-1-2 libx11-xcb1 libxcb1 libxcb-shm0 \
        libxcb-render0 libxcb-glx0 libxcb-shape0 libxtst6 libxt6 libpci3 \
        libasound2t64 libpulse0 libpango-1.0-0 libcairo2 libxcomposite1 \
        libxdamage1 libxfixes3 libxrandr2 libxkbcommon0 libnss3 libnspr4 \
        # Firefox renders through EGL on X11; Mesa's software one under Xvfb.
        libegl1 libegl-mesa0 \
        # Fonts. Beyond the basics, the scripts CI's test images cover (Arabic,
        # math, Korean, Japanese), so text renders like it does there.
        fontconfig fonts-liberation fonts-dejavu-core fonts-kacst-one \
        fonts-stix fonts-unfonts-core fonts-vlgothic \
        # Names libc frames in crash stacks.
        libc6-dbg \
        # Codecs, plus a PipeWire server used as a dummy audio sink/source by
        # media tests, and pactl to inspect it.
        ffmpeg pipewire wireplumber pipewire-pulse pulseaudio-utils \
        # Headless display and session bus, started by docker-entrypoint.sh.
        dbus-x11 x11-xserver-utils fluxbox xvfb \
    && rm -rf /var/lib/apt/lists/*

# Gecko media tests use the pipewire audiotestsrc plugin as a dummy sound
# source; it ships disabled. See firefox's
# taskcluster/docker/recipes/ubuntu2404-test-system-setup-base.sh.
RUN install -d -o root -g root -m 755 /etc/pipewire \
    && sed -e '/^context.spa-libs = {/,/^}$/ s/#\(audiotestsrc\)/\1/' \
        /usr/share/pipewire/pipewire.conf > /etc/pipewire/pipewire.conf

# The Firefox tree's agent skills profile with samply and analyze profiles with
# the Firefox Profiler's profiler-cli. samply and node come from `mach
# bootstrap`, profiler-cli from the stage above.
COPY --from=profiler-cli /opt/profiler-cli /opt/profiler-cli

# The tree's skills run Python helper scripts with `uv run`.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# hackbot.toml lives at the agent root (not inside the package), so copy it into
# the working dir; the runtime discovers it there (cwd) at startup.
COPY agents/bug-fix/hackbot.toml /app/hackbot.toml
COPY agents/bug-fix/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

RUN useradd --create-home --shell /bin/bash agent \
    && mkdir -p /workspace \
    && chown agent:agent /workspace \
    # Xvfb runs unprivileged and cannot create its own socket directory.
    && mkdir -p /tmp/.X11-unix \
    && chmod 1777 /tmp/.X11-unix \
    # Same identity hackbot_runtime commits with, so the agent's own `git commit`
    # and `git stash` work too instead of failing for lack of one.
    && git config --system user.name "Hackbot" \
    && git config --system user.email "hackbot@mozilla.tld"

ENV SCREEN_WIDTH=1920
ENV SCREEN_HEIGHT=1080
ENV SCREEN_DEPTH=24
# For Firefox started from the agent's shell (`./mach run`, mochitests); grizzly
# starts its own Xvfb for evaluate_testcase.
ENV DISPLAY=:99
# Home of the session bus and the PipeWire/PulseAudio sockets.
ENV XDG_RUNTIME_DIR=/tmp/runtime-agent
ENV DBUS_SESSION_BUS_ADDRESS=unix:path=/tmp/runtime-agent/bus

# `mach bootstrap` installs the toolchain here at runtime; put it on PATH so the
# agent's own `./mach build` (and the build_firefox tool) find rustc/clang,
# evaluate_testcase finds minidump-stackwalk to symbolize crashes, and the
# agent's shell finds node, samply and the developer tools (in ~/.cargo/bin),
# and the profiler-cli that runs with that node.
ENV PATH="/home/agent/.cargo/bin:/home/agent/.mozbuild/clang/bin:/home/agent/.mozbuild/minidump-stackwalk:/home/agent/.mozbuild/node/bin:/home/agent/.mozbuild/samply:/opt/profiler-cli/bin:${PATH}"
# Likewise for Android, once bootstrapped for it: java, adb, aapt2, apksigner,
# apkanalyzer, lint, sdkmanager. bootstrap_firefox links the versioned
# directories they are in as `current`/`latest`.
ENV PATH="${PATH}:/home/agent/.mozbuild/jdk/current/bin:/home/agent/.mozbuild/android-sdk-linux/platform-tools:/home/agent/.mozbuild/android-sdk-linux/build-tools/latest:/home/agent/.mozbuild/android-sdk-linux/cmdline-tools/latest/bin"

# Last, as it changes with any Python change: the layers above (system
# packages, mostly) stay cached across those.
COPY --from=builder /opt/venv /opt/venv

ARG HACKBOT_AGENT_COMMIT
ENV HACKBOT_AGENT_COMMIT=$HACKBOT_AGENT_COMMIT

USER agent

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["python", "-m", "hackbot_agents.bug_fix"]

FROM base AS broker

COPY --from=builder /opt/venv /opt/venv

RUN useradd --create-home --shell /bin/bash broker

USER broker

EXPOSE 8765

CMD ["python", "-m", "hackbot_agents.bug_fix.broker"]
