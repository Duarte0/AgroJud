FROM python:3.14.8-slim-trixie@sha256:f85c5697265c178cc6887276c55fe16cf3d14ca35c3df6a5eab3b360534a55d2 AS base

COPY --from=ghcr.io/astral-sh/uv:0.12.20@sha256:100047e74f30778ab704942321a09750d6158739573ff58bf3924085cc6cd2d8 /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY pyproject.toml uv.lock .python-version alembic.ini ./
COPY src ./src

RUN uv sync --frozen --no-dev

FROM base AS production

EXPOSE 8000

CMD ["uv", "run", "--no-sync", "uvicorn", "agrojud.api.app:app", "--host", "0.0.0.0", "--port", "8000"]

FROM base AS development

RUN uv sync --frozen --all-groups
COPY tests ./tests

CMD ["uv", "run", "--no-sync", "pytest"]
