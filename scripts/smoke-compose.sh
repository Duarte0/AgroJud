#!/usr/bin/env sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
MODE=${1:-}

case "$MODE" in
  demo)
    PROJECT=agrojud-demo
    ENV_FILE="$ROOT_DIR/.env.demo"
    ;;
  real)
    PROJECT=agrojud-real
    ENV_FILE="$ROOT_DIR/.env.real"
    ;;
  *)
    echo "Usage: $0 demo|real" >&2
    exit 2
    ;;
esac

if [ ! -f "$ENV_FILE" ]; then
  echo "Missing $ENV_FILE. Copy the matching .env.*.example first." >&2
  exit 2
fi

ENV_MODE=$(sed -n 's/^AGROJUD_ENV=//p' "$ENV_FILE" | sed -n '1p')
ENV_PROJECT=$(sed -n 's/^COMPOSE_PROJECT_NAME=//p' "$ENV_FILE" | sed -n '1p')
ENV_DATABASE=$(sed -n 's/^POSTGRES_DB=//p' "$ENV_FILE" | sed -n '1p')
if [ "$ENV_MODE" != "$MODE" ] || [ "$ENV_PROJECT" != "$PROJECT" ] || \
   [ "$ENV_DATABASE" != "agrojud_$MODE" ]; then
  echo "The environment file does not match the selected $MODE project/database." >&2
  exit 2
fi

API_PORT=$(sed -n 's/^API_PORT=//p' "$ENV_FILE")
API_PORT=${API_PORT:-8000}
FRONTEND_PORT=$(sed -n 's/^FRONTEND_PORT=//p' "$ENV_FILE")
FRONTEND_PORT=${FRONTEND_PORT:-5173}

compose() {
  docker compose --project-name "$PROJECT" --env-file "$ENV_FILE" -f "$ROOT_DIR/compose.yaml" "$@"
}

compose up --build --detach --wait db
compose run --rm --no-deps api uv run --no-sync alembic upgrade head
if [ "$MODE" = demo ]; then
  compose --profile worker up --build --detach --wait api frontend worker
  compose --profile worker run --rm --no-deps worker uv run --no-sync agrojud-worker --check
else
  compose up --build --detach --wait api frontend
fi
curl --fail --silent --show-error "http://127.0.0.1:$API_PORT/api/v1/health/live"
curl --fail --silent --show-error "http://127.0.0.1:$API_PORT/api/v1/health/ready"
curl --fail --silent --show-error "http://127.0.0.1:$FRONTEND_PORT/" >/dev/null
curl --fail --silent --show-error "http://127.0.0.1:$FRONTEND_PORT/processes" >/dev/null
curl --fail --silent --show-error "http://127.0.0.1:$FRONTEND_PORT/api/v1/health/live"
compose ps
