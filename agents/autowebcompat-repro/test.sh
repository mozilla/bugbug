#!/usr/bin/env bash

set -ex

fix=0
for arg in "$@"; do
    case "$arg" in
        --fix) fix=1 ;;
    esac
done

uv sync --extra=test

if [ "$fix" -eq 1 ]; then
    uv run ruff format
    uv run ruff check --fix
else
    uv run ruff format --check
    uv run ruff check
fi

uv run ty check
uv run mypy .
