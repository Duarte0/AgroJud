#!/usr/bin/env sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
ENV_FILE=${1:-"$ROOT_DIR/.env.test"}
PROJECT=agrojud-test

if [ ! -f "$ENV_FILE" ]; then
  echo "Missing $ENV_FILE. Copy .env.test.example first." >&2
  exit 2
fi

POSTGRES_USER=$(sed -n 's/^POSTGRES_USER=//p' "$ENV_FILE")
POSTGRES_DB=$(sed -n 's/^POSTGRES_DB=//p' "$ENV_FILE")
if [ -z "$POSTGRES_USER" ] || [ -z "$POSTGRES_DB" ]; then
  echo "The test environment file must define POSTGRES_USER and POSTGRES_DB." >&2
  exit 2
fi

compose() {
  docker compose --project-name "$PROJECT" --env-file "$ENV_FILE" -f "$ROOT_DIR/compose.test.yaml" --profile test "$@"
}

wait_for_database() {
  attempt=0
  while [ "$attempt" -lt 30 ]; do
    if compose exec -T db pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; then
      return 0
    fi
    attempt=$((attempt + 1))
    sleep 1
  done
  echo "Test database did not become ready." >&2
  return 1
}

probe_key="spec001-$(date +%s)-$$"
compose up --detach --wait db
compose run --rm --build tests uv run --no-sync agrojud-persistence-probe write "$probe_key"
compose stop db
compose start db
wait_for_database
compose run --rm tests uv run --no-sync agrojud-persistence-probe read "$probe_key"
compose run --rm tests uv run --no-sync agrojud-persistence-probe cleanup
