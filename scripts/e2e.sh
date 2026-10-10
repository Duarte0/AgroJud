#!/usr/bin/env sh
# Runs the Playwright suite against an isolated demo stack (project name includes e2e).
# The database uses tmpfs and is discarded when the stack goes down.
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
ENV_FILE=${E2E_ENV_FILE:-"$ROOT_DIR/.env.e2e"}

if [ ! -f "$ENV_FILE" ]; then
  echo "Missing $ENV_FILE. Copy .env.e2e.example first." >&2
  exit 2
fi

PROJECT=${E2E_COMPOSE_PROJECT:-$(sed -n 's/^COMPOSE_PROJECT_NAME=//p' "$ENV_FILE")}
MODE=$(sed -n 's/^AGROJUD_ENV=//p' "$ENV_FILE")
DATABASE=$(sed -n 's/^POSTGRES_DB=//p' "$ENV_FILE")
DATAJUD_KEY=$(sed -n 's/^DATAJUD_API_KEY=//p' "$ENV_FILE")
API_PORT=${E2E_API_PORT:-$(sed -n 's/^API_PORT=//p' "$ENV_FILE")}
FRONTEND_PORT=${E2E_FRONTEND_PORT:-$(sed -n 's/^FRONTEND_PORT=//p' "$ENV_FILE")}
API_PORT=${API_PORT:-18765}
FRONTEND_PORT=${FRONTEND_PORT:-18766}
case "$API_PORT:$FRONTEND_PORT" in
  *[!0-9:]*|:*) echo "API_PORT and FRONTEND_PORT must be numeric ports." >&2; exit 2 ;;
esac
export API_PORT FRONTEND_PORT
FRONTEND_ORIGIN="http://127.0.0.1:$FRONTEND_PORT"
export FRONTEND_ORIGIN
case "$PROJECT" in
  *e2e*) ;;
  *)
    echo "Refusing to run browser tests outside an *e2e* Compose project." >&2
    exit 2
    ;;
esac
if [ "$MODE" != demo ] || [ "$DATABASE" != agrojud_e2e ] || [ -n "$DATAJUD_KEY" ]; then
  echo "Browser tests require AGROJUD_ENV=demo, POSTGRES_DB=agrojud_e2e and no DataJud key." >&2
  exit 2
fi

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

compose build api frontend
compose up --detach --wait db
compose run --rm --no-deps api uv run --no-sync alembic upgrade head
compose up --detach --wait api frontend worker

cd "$ROOT_DIR/frontend"
PLAYWRIGHT_BASE_URL="http://127.0.0.1:$FRONTEND_PORT" \
  E2E_ENV_FILE="$ENV_FILE" \
  E2E_COMPOSE_PROJECT="$PROJECT" \
  npx playwright test "$@"
