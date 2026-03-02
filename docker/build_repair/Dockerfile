# ./mach taskgraph load-image
FROM debian12-amd64-build

WORKDIR /app

RUN apt-get install -y python3-pip python3-venv

RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN apt-get install -y git nodejs npm && rm -rf /var/lib/apt/lists/*
RUN pip install weave>=0.52.29 pydantic claude-agent-sdk requests

COPY . /app

ENV FIREFOX_GIT_REPO=/workspace/firefox
