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
    uv sync --frozen --no-dev --no-install-workspace --package hackbot-agent-autowebcompat-repro

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,target=/app,rw \
    uv sync --locked --no-dev --no-editable --package hackbot-agent-autowebcompat-repro

FROM python:3.14 AS base

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
        # Codecs and audio/video related dependencies
        ffmpeg \
        # Window manager and display backend
        fluxbox \
        xvfb \
    && rm -rf /var/lib/apt/lists/*

# Install the DevTools MCP servers and Puppeteer from the pinned
# package.json/lockfile.
COPY agents/autowebcompat-repro/package.json \
     agents/autowebcompat-repro/package-lock.json \
     agents/autowebcompat-repro/repro_reference.mjs \
     /app/repro/
RUN cd /app/repro && npm ci --omit=dev

# hackbot.toml lives at the agent root (not inside the package), so copy it into
# the working dir; the runtime discovers it there (cwd) at startup.
COPY agents/autowebcompat-repro/hackbot.toml /app/hackbot.toml

RUN useradd --create-home --shell /bin/bash agent \
    && mkdir -p /workspace \
    && chown agent:agent /workspace /app/repro

ENV SCREEN_WIDTH=1280
ENV SCREEN_HEIGHT=1024
ENV SCREEN_DEPTH=24
ENV DISPLAY=:99.0

USER agent

COPY --from=builder /opt/venv /opt/venv

CMD ["python", "-m", "hackbot_agents.autowebcompat_repro"]

FROM agent AS android-base

USER root

ENV ANDROID_HOME=/opt/android-sdk \
    ANDROID_USER_HOME=/home/agent/.android \
    ANDROID_AVD_HOME=/home/agent/.android/avd \
    ADB_VENDOR_KEYS=/home/agent/.android/adbkey
ENV PATH="/opt/android-sdk/platform-tools:/opt/android-sdk/emulator:$PATH"

ARG TARGETARCH
RUN test "$TARGETARCH" = amd64 \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        libpulse0 libglu1-mesa \
    && rm -rf /var/lib/apt/lists/*

FROM android-base AS android-sdk

ARG ANDROID_API=37.0
ARG CMDLINE_TOOLS_VERSION=15859902

ENV PATH="/opt/android-sdk/cmdline-tools/latest/bin:$PATH"

RUN apt-get update \
    && apt-get install -y --no-install-recommends default-jre-headless curl unzip \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    mkdir -p "$ANDROID_HOME/cmdline-tools"; \
    curl --fail --location --retry 5 --retry-delay 5 --retry-max-time 300 \
        --connect-timeout 30 --max-time 180 \
        -o /tmp/cmdline-tools.zip \
        "https://dl.google.com/android/repository/commandlinetools-linux-${CMDLINE_TOOLS_VERSION}_latest.zip"; \
    unzip -q /tmp/cmdline-tools.zip -d "$ANDROID_HOME/cmdline-tools"; \
    mv "$ANDROID_HOME/cmdline-tools/cmdline-tools" "$ANDROID_HOME/cmdline-tools/latest"; \
    rm /tmp/cmdline-tools.zip; \
    yes | sdkmanager --licenses > /dev/null; \
    image="system-images;android-${ANDROID_API};google_apis_playstore;x86_64"; \
    sdkmanager --install "platform-tools" "emulator" "$image"; \
    mkdir -p "$ANDROID_AVD_HOME"; \
    echo no | avdmanager create avd --force --name "webcompat" \
        --device "pixel_9" --package "$image" --path "$ANDROID_AVD_HOME/webcompat.avd"; \
    printf 'disk.dataPartition.size=12G\nhw.ramSize=8192\nhw.keyboard=no\n' \
        >> "$ANDROID_AVD_HOME/webcompat.avd/config.ini"

FROM android-base AS android-agent

# Java and the SDK command-line tools are only needed to prepare the AVD.
COPY --from=android-sdk /opt/android-sdk/platform-tools /opt/android-sdk/platform-tools
COPY --from=android-sdk /opt/android-sdk/system-images /opt/android-sdk/system-images
COPY --from=android-sdk --chown=agent:agent /opt/android-sdk/emulator /opt/android-sdk/emulator
COPY --from=android-sdk --chown=agent:agent /home/agent/.android /home/agent/.android

USER agent

FROM base AS broker

RUN useradd --create-home --shell /bin/bash broker

USER broker

EXPOSE 8765

COPY --from=builder /opt/venv /opt/venv

CMD ["python", "-m", "hackbot_agents.autowebcompat_repro.broker"]
