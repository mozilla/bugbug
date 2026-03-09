# Build stage
FROM python:3.12 AS builder

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Copy dependency files
COPY pyproject.toml uv.lock ./

# Install dependencies into .venv
RUN uv sync --locked --no-dev

# Runtime stage
FROM python:3.12-slim

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv

# Copy application code
COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini ./

# Enable unbuffered mode will ensure real-time logging
ENV PYTHONUNBUFFERED=1
ENV PORT=8080
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8080

# Run the server
CMD uvicorn app.main:app --host 0.0.0.0 --port $PORT
