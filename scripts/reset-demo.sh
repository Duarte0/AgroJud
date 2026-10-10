#!/usr/bin/env sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [ "${1:-}" != --target ] || [ "${2:-}" != demo ] || [ "${3:-}" != --confirm ] || [ "$#" -ne 3 ]; then
  echo "Usage: $0 --target demo --confirm" >&2
  echo "This command deletes all data in the agrojud-demo database." >&2
  exit 2
fi

ENV_FILE="$ROOT_DIR/.env.demo"
if [ ! -f "$ENV_FILE" ]; then
  echo "Missing $ENV_FILE; reset is restricted to the configured demo environment." >&2
  exit 2
fi

env_value() {
  sed -n "s/^$1=//p" "$ENV_FILE" | sed -n '1p'
}
if [ "$(env_value AGROJUD_ENV)" != demo ] || \
   [ "$(env_value COMPOSE_PROJECT_NAME)" != agrojud-demo ] || \
   [ "$(env_value POSTGRES_DB)" != agrojud_demo ]; then
  echo "Refusing reset: .env.demo must identify agrojud-demo/agrojud_demo with AGROJUD_ENV=demo." >&2
  exit 2
fi

compose() {
  docker compose --project-name agrojud-demo --env-file "$ENV_FILE" --profile worker \
    -f "$ROOT_DIR/compose.yaml" "$@"
}
for SERVICE in api frontend worker; do
  if [ -n "$(compose ps -q "$SERVICE")" ]; then
    echo "Stop the demo $SERVICE before resetting; the database will not be changed." >&2
    exit 2
  fi
done

DATABASE=$(compose exec -T db sh -ec 'psql -X --set=ON_ERROR_STOP=1 --tuples-only --no-align \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --command "select current_database()"' | tr -d '\r')
if [ "$DATABASE" != agrojud_demo ]; then
  echo "Refusing reset: connected database was not agrojud_demo." >&2
  exit 1
fi

compose exec -T db sh -ec 'psql -X --set=ON_ERROR_STOP=1 --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" --command "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"'
echo "The demo database is empty. Apply migrations explicitly before starting the app."
