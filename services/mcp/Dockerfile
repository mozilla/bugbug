# Build stage
FROM python:3.12 AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN uv sync --locked --no-dev

# Resource generation stage
FROM builder AS resource-generator

COPY scripts/llms-txt.py ./scripts/

RUN mkdir static && uv run --extra llms-txt scripts/llms-txt.py


# Runtime stage
FROM python:3.12-slim

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=resource-generator /app/static/llms.txt /app/static/llms.txt

COPY src .

ENV PATH="/app/.venv/bin:$PATH"

# Enable unbuffered mode will ensure real-time logging
ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "bugbug_mcp.server"]
