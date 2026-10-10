#!/usr/bin/env sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
MODE=${1:-}
ARCHIVE=${2:-}
PROJECT=${3:-}
case "$MODE" in
  demo|real) ;;
  *) echo "Usage: $0 demo|real archive.dump agrojud-restore-NAME" >&2; exit 2 ;;
esac
case "$PROJECT" in
  agrojud-restore-[A-Za-z0-9]* ) ;;
  *) echo "Restore must use a new agrojud-restore-* Compose project." >&2; exit 2 ;;
esac
case "$PROJECT" in
  *[!A-Za-z0-9_-]*) echo "Invalid Compose project name." >&2; exit 2 ;;
esac
if [ -z "$ARCHIVE" ] || [ ! -f "$ARCHIVE" ]; then
  echo "Backup archive does not exist." >&2
  exit 2
fi
case "$ARCHIVE" in
  /*) ;;
  *) ARCHIVE="$ROOT_DIR/$ARCHIVE" ;;
esac
MANIFEST="$ARCHIVE.manifest"
if [ ! -f "$MANIFEST" ]; then
  echo "Backup manifest is missing; refusing an unverifiable restore." >&2
  exit 2
fi

manifest_value() {
  awk -F= -v key="$1" '$1 == key { print substr($0, index($0, "=") + 1); count++ } END { if (count != 1) exit 1 }' "$MANIFEST"
}

if [ "$(manifest_value format)" != agrojud-postgres-custom-v1 ]; then
  echo "Unsupported backup manifest format." >&2
  exit 2
fi
if [ "$(manifest_value environment)" != "$MODE" ]; then
  echo "The selected environment does not match the backup manifest." >&2
  exit 2
fi
EXPECTED_SHA=$(manifest_value sha256)
ACTUAL_SHA=$(sha256sum "$ARCHIVE" | awk '{print $1}')
if [ "$EXPECTED_SHA" != "$ACTUAL_SHA" ]; then
  echo "Backup checksum mismatch; refusing restore." >&2
  exit 2
fi

for LABEL in container network volume; do
  case "$LABEL" in
    container) EXISTING=$(docker ps -a --filter "label=com.docker.compose.project=$PROJECT" -q) ;;
    network) EXISTING=$(docker network ls --filter "label=com.docker.compose.project=$PROJECT" -q) ;;
    volume) EXISTING=$(docker volume ls --filter "label=com.docker.compose.project=$PROJECT" -q) ;;
  esac
  if [ -n "$EXISTING" ]; then
    echo "Restore project already has $LABEL resources; choose a new project name." >&2
    exit 2
  fi
done

umask 077
ENV_TMP=$(mktemp "${TMPDIR:-/tmp}/agrojud-restore-env.XXXXXX")
RESTORE_PASSWORD=$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')
cat > "$ENV_TMP" <<EOF
COMPOSE_PROJECT_NAME=$PROJECT
AGROJUD_ENV=$MODE
API_PORT=18799
FRONTEND_PORT=18798
POSTGRES_DB=agrojud_restore
POSTGRES_USER=agrojud_restore
POSTGRES_PASSWORD=$RESTORE_PASSWORD
DATABASE_URL=postgresql+psycopg://agrojud_restore:$RESTORE_PASSWORD@db:5432/agrojud_restore
DATAJUD_API_KEY=
JOB_LEASE_SECONDS=120
JOB_HEARTBEAT_SECONDS=20
JOB_POLL_SECONDS=2
EXPORT_PROCESS_LIMIT=50000
FRONTEND_ORIGIN=http://127.0.0.1:18798
EOF
unset RESTORE_PASSWORD

RESTORE_STARTED=1
TABLE_DUMP_TMP=$(mktemp "${TMPDIR:-/tmp}/agrojud-restore-check.XXXXXX")
cleanup() {
  if [ "$RESTORE_STARTED" -eq 1 ]; then
    docker compose --project-name "$PROJECT" --env-file "$ENV_TMP" \
      -f "$ROOT_DIR/compose.yaml" -f "$ROOT_DIR/compose.restore.yaml" \
      down --remove-orphans >/dev/null 2>&1 || true
  fi
  rm -f "$ENV_TMP" "$TABLE_DUMP_TMP"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

compose() {
  docker compose --project-name "$PROJECT" --env-file "$ENV_TMP" \
    -f "$ROOT_DIR/compose.yaml" -f "$ROOT_DIR/compose.restore.yaml" "$@"
}

compose up --detach --wait db
compose exec -T db sh -ec 'pg_restore --no-owner --no-privileges --exit-on-error \
  --single-transaction --username "$POSTGRES_USER" --dbname "$POSTGRES_DB"' < "$ARCHIVE"

RESTORED_STATS=$(compose exec -T db sh -ec 'psql -X --set=ON_ERROR_STOP=1 --tuples-only --no-align \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB"' <<'SQL'
SELECT 'database=' || current_database()
UNION ALL SELECT 'alembic_revision=' || COALESCE((SELECT string_agg(version_num, ',' ORDER BY version_num) FROM alembic_version), 'none')
UNION ALL SELECT 'processes=' || count(*)::text FROM processes
UNION ALL SELECT 'representations=' || count(*)::text FROM representations
UNION ALL SELECT 'representation_versions=' || count(*)::text FROM representation_versions
UNION ALL SELECT 'collections=' || count(*)::text FROM collections
UNION ALL SELECT 'collection_observations=' || count(*)::text FROM collection_observations
UNION ALL SELECT 'collection_results=' || count(*)::text FROM collection_results
UNION ALL SELECT 'movement_occurrences=' || count(*)::text FROM movement_occurrences
UNION ALL SELECT 'movement_snapshots=' || count(*)::text FROM movement_snapshots
UNION ALL SELECT 'quarantine_rejections=' || count(*)::text FROM quarantine_rejections
UNION ALL SELECT 'triage_decisions=' || count(*)::text FROM process_triage
UNION ALL SELECT 'triage_history=' || count(*)::text FROM process_triage_history
UNION ALL SELECT 'watchlist_entries=' || count(*)::text FROM process_watchlist_entries
UNION ALL SELECT 'watchlist_history=' || count(*)::text FROM process_watchlist_history
UNION ALL SELECT 'jobs=' || count(*)::text FROM jobs
UNION ALL SELECT 'job_attempts=' || count(*)::text FROM job_attempts
UNION ALL SELECT 'job_checkpoints=' || count(*)::text FROM job_checkpoints
UNION ALL SELECT 'process_news=' || count(*)::text FROM process_news
UNION ALL SELECT 'process_signals=' || count(*)::text FROM process_signals
UNION ALL SELECT 'database_bytes=' || pg_database_size(current_database())::text;
SQL
)

for KEY in alembic_revision processes representations representation_versions collections \
  collection_observations collection_results movement_occurrences movement_snapshots \
  quarantine_rejections triage_decisions triage_history watchlist_entries watchlist_history \
  jobs job_attempts job_checkpoints process_news process_signals; do
  EXPECTED=$(manifest_value "$KEY")
  RESTORED=$(printf '%s\n' "$RESTORED_STATS" | sed -n "s/^$KEY=//p" | sed -n '1p')
  if [ "$EXPECTED" != "$RESTORED" ]; then
    echo "Restore verification failed for $KEY (expected $EXPECTED, obtained $RESTORED)." >&2
    exit 1
  fi
done

table_digest() {
  TABLE_NAME=$1
  ORDER_COLUMN=$2
  case "$TABLE_NAME:$ORDER_COLUMN" in
    processes:id|representations:id|representation_versions:id|collections:id|\
    representation_subjects:id|collection_observations:id|collection_results:id|\
    movement_occurrences:id|movement_snapshots:id|movement_snapshot_occurrences:id|\
    quarantine_rejections:id|quarantine_resolutions:id|process_triage:process_id|\
    process_triage_history:id|process_watchlist_entries:process_id|\
    process_watchlist_history:id|process_watch_cycles:id|\
    representation_watch_baselines:id|process_news:id|jobs:id|job_attempts:id|\
    job_events:id|job_checkpoints:id|saved_searches:id|saved_search_versions:id|\
    schedule_dispatches:id|queue_claim_state:key|source_rate_limits:source|\
    signal_run_processes:id|signal_run_inputs:id|process_signals:id|signal_evaluations:id) ;;
    *) echo "Internal table digest allowlist rejected $TABLE_NAME." >&2; return 2 ;;
  esac
  compose exec -T db sh -ec "psql -X --set=ON_ERROR_STOP=1 --tuples-only --no-align \\
    --username \"\$POSTGRES_USER\" --dbname \"\$POSTGRES_DB\" \\
    --command 'SELECT to_jsonb(row_data)::text FROM (SELECT * FROM $TABLE_NAME ORDER BY $ORDER_COLUMN) AS row_data'" \
    > "$TABLE_DUMP_TMP"
  sha256sum "$TABLE_DUMP_TMP" | awk '{print $1}'
}

for TABLE in processes representations representation_versions representation_subjects \
  collections collection_observations collection_results movement_occurrences movement_snapshots \
  movement_snapshot_occurrences quarantine_rejections quarantine_resolutions process_triage \
  process_triage_history process_watchlist_entries process_watchlist_history process_watch_cycles \
  representation_watch_baselines process_news jobs job_attempts job_events job_checkpoints \
  saved_searches saved_search_versions schedule_dispatches queue_claim_state source_rate_limits \
  signal_run_processes signal_run_inputs process_signals signal_evaluations; do
  case "$TABLE" in
    process_triage|process_watchlist_entries) ORDER_COLUMN=process_id ;;
    queue_claim_state) ORDER_COLUMN=key ;;
    source_rate_limits) ORDER_COLUMN=source ;;
    *) ORDER_COLUMN=id ;;
  esac
  KEY="${TABLE}_sha256"
  EXPECTED=$(manifest_value "$KEY")
  ACTUAL=$(table_digest "$TABLE" "$ORDER_COLUMN")
  if [ "$EXPECTED" != "$ACTUAL" ]; then
    echo "Restore content verification failed for $TABLE." >&2
    exit 1
  fi
done

compose build api
RESTORE_HEAD=$(compose run --rm --no-deps api uv run --no-sync alembic heads \
  | awk '$2 == "(head)" { print $1 }' | sort | paste -sd, -)
RESTORED_REVISION=$(printf '%s\n' "$RESTORED_STATS" | sed -n 's/^alembic_revision=//p')
if [ -z "$RESTORE_HEAD" ] || [ "$RESTORED_REVISION" != "$RESTORE_HEAD" ]; then
  echo "Restore migration mismatch: archive=$RESTORED_REVISION current_code=$RESTORE_HEAD." >&2
  exit 1
fi

printf 'Restore verified in disposable project %s: source=%s revision=%s\n' \
  "$PROJECT" "$MODE" "$RESTORED_REVISION"
printf 'Rows: processes=%s representations=%s versions=%s triage=%s watchlist=%s jobs=%s\n' \
  "$(printf '%s\n' "$RESTORED_STATS" | sed -n 's/^processes=//p')" \
  "$(printf '%s\n' "$RESTORED_STATS" | sed -n 's/^representations=//p')" \
  "$(printf '%s\n' "$RESTORED_STATS" | sed -n 's/^representation_versions=//p')" \
  "$(printf '%s\n' "$RESTORED_STATS" | sed -n 's/^triage_decisions=//p')" \
  "$(printf '%s\n' "$RESTORED_STATS" | sed -n 's/^watchlist_entries=//p')" \
  "$(printf '%s\n' "$RESTORED_STATS" | sed -n 's/^jobs=//p')"
echo "The restore project used tmpfs; API, frontend and worker remained stopped."
