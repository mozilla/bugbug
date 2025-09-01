# Build stage
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN uv sync --locked --no-dev

# Runtime stage
FROM python:3.12-slim

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv

COPY src .

ENV PATH="/app/.venv/bin:$PATH"

# Enable unbuffered mode will ensure real-time logging
ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "server"]
