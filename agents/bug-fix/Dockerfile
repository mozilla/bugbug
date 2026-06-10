FROM python:3.12 AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Workspace root metadata first so the dep-download layer caches independently
# of source changes. Member pyproject.toml files aren't needed here: --frozen
# trusts uv.lock as-is, so no per-member lockfile validation is required.
COPY pyproject.toml uv.lock VERSION ./

# Install external deps without building workspace members.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace --package hackbot-agent-bug-fix

# Bring in the full workspace and install the package itself non-editable, so
# the build artifact lands in the venv and the final image needs no source tree.
COPY . .

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --package hackbot-agent-bug-fix

FROM python:3.12 AS base

COPY --from=builder /app/.venv /app/.venv
WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PATH="/app/.venv/bin:$PATH"

FROM base AS agent

RUN useradd --create-home --shell /bin/bash agent \
    && mkdir -p /workspace \
    && chown agent:agent /workspace

USER agent

CMD ["python", "-m", "agent"]

FROM base AS broker

RUN useradd --create-home --shell /bin/bash broker

USER broker

EXPOSE 8765

CMD ["python", "-m", "broker"]
