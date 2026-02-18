FROM python:3.12-slim

RUN apt-get update && apt-get install -y git nodejs npm && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app
RUN pip install -e .
RUN pip install claude-agent-sdk jupyter

ENV FIREFOX_GIT_REPO=/workspace/firefox

ENTRYPOINT ["python", "scripts/build_repair_eval.py"]
