FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock* ./
RUN uv sync --locked --no-dev || uv sync --no-dev

FROM python:3.12-slim

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY app/ ./app/

ENV PYTHONUNBUFFERED=1
ENV PORT=8080
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
