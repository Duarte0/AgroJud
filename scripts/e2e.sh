#!/usr/bin/env sh
# Runs the Playwright suite against an isolated demo stack (project agrojud-e2e).
# The database uses tmpfs and is discarded when the stack goes down.
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
ENV_FILE=${E2E_ENV_FILE:-"$ROOT_DIR/.env.e2e"}

if [ ! -f "$ENV_FILE" ]; then
  echo "Missing $ENV_FILE. Copy .env.e2e.example first." >&2
  exit 2
fi

PROJECT=$(sed -n 's/^COMPOSE_PROJECT_NAME=//p' "$ENV_FILE")
API_PORT=$(sed -n 's/^API_PORT=//p' "$ENV_FILE")
case "$PROJECT" in
  *e2e*) ;;
  *)
    echo "Refusing to run browser tests outside an *e2e* Compose project." >&2
    exit 2
    ;;
esac

compose() {
  docker compose --project-name "$PROJECT" --env-file "$ENV_FILE" \
    -f "$ROOT_DIR/compose.yaml" -f "$ROOT_DIR/compose.e2e.yaml" --profile worker "$@"
}

cleanup() {
  if [ "${E2E_KEEP_STACK:-0}" != "1" ]; then
    compose down --remove-orphans
  fi
}
trap cleanup EXIT

compose build api
compose up --detach --wait db
compose run --rm api uv run --no-sync alembic upgrade head
compose up --detach --wait api
compose up --detach worker

cd "$ROOT_DIR/frontend"
AGROJUD_API_URL="http://127.0.0.1:$API_PORT" \
  E2E_ENV_FILE="$ENV_FILE" \
  E2E_COMPOSE_PROJECT="$PROJECT" \
  npx playwright test "$@"
