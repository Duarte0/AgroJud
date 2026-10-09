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

API_PORT=$(sed -n 's/^API_PORT=//p' "$ENV_FILE")
API_PORT=${API_PORT:-8000}

compose() {
  docker compose --project-name "$PROJECT" --env-file "$ENV_FILE" -f "$ROOT_DIR/compose.yaml" "$@"
}

compose up --build --detach db api
compose run --rm api uv run --no-sync alembic upgrade head
compose up --detach --wait api
compose run --rm worker
curl --fail --silent --show-error "http://127.0.0.1:$API_PORT/api/v1/health/live"
curl --fail --silent --show-error "http://127.0.0.1:$API_PORT/api/v1/health/ready"
compose ps
