# Load the base image by running this from the Firefox repo:
# ./mach taskgraph load-image --task-id aQDejwXUQsSHxvwE2qQcQg
FROM debian12-amd64-build

WORKDIR /app

RUN apt-get update && \
    apt-get install -y git nodejs npm build-essential zlib1g-dev \
    libncurses5-dev libgdbm-dev libnss3-dev libssl-dev libreadline-dev \
    libffi-dev libsqlite3-dev wget libbz2-dev && \
    rm -rf /var/lib/apt/lists/*

# bugbug requires Python 3.12 and there's no package for Debian 12
RUN wget https://www.python.org/ftp/python/3.12.8/Python-3.12.8.tgz && \
    tar -xf Python-3.12.8.tgz && \
    cd Python-3.12.8 && \
    ./configure --enable-optimizations --prefix=/usr/local && \
    make -j$(nproc) && \
    make install && \
    cd .. && rm -rf Python-3.12.8 Python-3.12.8.tgz

RUN python3.12 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY requirements.txt /app/
RUN uv pip install -r /app/requirements.txt

COPY . /app

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV FIREFOX_GIT_REPO=/workspace/firefox